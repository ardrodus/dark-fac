"""TDD Feature Writer — implement feature code to make failing tests pass.

Ports Stage 2 of tdd-pipeline.sh.  The agent prompt includes PRD, design doc,
interface definitions, and test NAMES + RESULTS (pass/fail) but **excludes**
test source code (information gap principle).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.workspace.manager import Workspace

from factory.pipeline.tdd.test_writer import SpecBundle

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 300


@dataclass(frozen=True, slots=True)
class TestRunResult:
    """Outcome of a test run — names and pass/fail status, NO source code."""

    passed: bool
    total: int = 0
    failures: int = 0
    test_names: tuple[str, ...] = ()
    failure_messages: tuple[str, ...] = ()
    raw_output: str = ""


@dataclass(frozen=True, slots=True)
class FeatureWriterResult:
    """Outcome of a Feature Writer invocation."""

    files_modified: tuple[str, ...] = ()
    files_created: tuple[str, ...] = ()
    implementation_summary: str = ""
    committed: bool = False
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _build_prompt(
    specs: SpecBundle,
    workspace_path: str,
    test_results: TestRunResult,
) -> str:
    """Assemble prompt from *specs* and *test_results*.  Never includes test source."""
    parts: list[str] = [
        "You are the Feature Writer agent in a TDD pipeline.",
        "Implement the minimum code necessary to make all failing tests pass.",
        "Do NOT create or modify test files.\n",
        "INFORMATION GAP PRINCIPLE: You have NOT been given any test source",
        "code. You can only see test NAMES and their pass/fail RESULTS.\n",
    ]
    _artifact_sections = [
        ("prd", "## PRD / User Story"),
        ("design_doc", "## Technical Design Document"),
        ("api_contract", "## API Contract"),
        ("schema_spec", "## Schema Specification"),
        ("interface_definitions", "## Interface Definitions"),
    ]
    for attr, heading in _artifact_sections:
        content = getattr(specs, attr)
        if content:
            parts.append(f"{heading}\n\n{content}\n")
    parts.append("## Test Results\n")
    if test_results.test_names:
        parts.append("Test names and status:")
        for name in test_results.test_names:
            parts.append(f"  - {name}")
    if test_results.failure_messages:
        parts.append("\nFailure details (names and messages only):")
        for msg in test_results.failure_messages:
            parts.append(f"  - {msg}")
    parts.append(
        f"\nTotal: {test_results.total}  Failures: {test_results.failures}"
    )
    parts.extend([
        "\n## Instructions\n",
        "- Implement feature code to make ALL tests pass.",
        "- Write implementation code only — do NOT create or modify test files.",
        "- You CANNOT see test source code — only names and results above.",
        "- Follow design artifacts exactly when provided.",
        "- Write the minimum code necessary.\n",
        "## Output Format\n",
        "Output a JSON object: "
        '{"files_modified": [...], "files_created": [...], '
        '"implementation_summary": "..."}',
        f"\nWorkspace root: {workspace_path}",
    ])
    return "\n".join(parts)


def _invoke_agent(
    prompt: str, workspace_path: str, *,
    invoke_fn: Callable[[str], str] | None = None,
) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415

    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, check=True, cwd=workspace_path,
    ).stdout


def _parse_result(raw: str) -> tuple[list[str], list[str], str]:
    """Extract structured fields from agent JSON output."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(
        r"\{[^{}]*\"(?:files_modified|files_created)\"[^{}]*\}",
        text, re.DOTALL,
    )
    if match:
        text = match.group(0)
    data: dict[str, object] = json.loads(text)
    raw_modified = data.get("files_modified", [])
    modified = [str(f) for f in raw_modified] if isinstance(raw_modified, list) else []
    raw_created = data.get("files_created", [])
    created = [str(f) for f in raw_created] if isinstance(raw_created, list) else []
    summary = str(data.get("implementation_summary", ""))
    return modified, created, summary


def _commit_implementation(workspace_path: str, files: list[str]) -> bool:
    """Stage and commit implementation files to the workspace branch."""
    if not files:
        return False
    from factory.integrations.shell import git  # noqa: PLC0415

    for f in files:
        git(["add", f], cwd=workspace_path, check=False)
    if not git(["diff", "--cached", "--name-only"], cwd=workspace_path).stdout.strip():
        return False
    result = git(
        ["commit", "-m", "feat: implement feature code (Feature Writer agent)"],
        cwd=workspace_path, check=False,
    )
    if result.returncode != 0:
        logger.warning("git commit failed: %s", result.stderr.strip())
        return False
    logger.info("Committed implementation files to workspace branch")
    return True


def run_feature_writer(
    specs: SpecBundle,
    workspace: Workspace,
    test_results: TestRunResult,
    *,
    invoke_fn: Callable[[str], str] | None = None,
) -> FeatureWriterResult:
    """Implement feature code in *workspace* to make tests pass.

    Prompt includes PRD, design doc, interface definitions, and test NAMES
    with pass/fail results.  Excludes test source code (information gap).
    """
    ws_path = workspace.path
    prompt = _build_prompt(specs, ws_path, test_results)
    errors: list[str] = []
    try:
        raw = _invoke_agent(prompt, ws_path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Feature Writer agent failed: %s", exc)
        return FeatureWriterResult(raw_output="", errors=(str(exc),))
    try:
        modified, created, summary = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Failed to parse Feature Writer output: %s", exc)
        modified, created, summary = [], [], ""
        errors.append(f"parse error: {exc}")
    existing_mod = [f for f in modified if Path(ws_path, f).exists()]
    existing_new = [f for f in created if Path(ws_path, f).exists()]
    if len(existing_mod) < len(modified):
        errors.append(f"{len(modified) - len(existing_mod)} modified files not found")
    if len(existing_new) < len(created):
        errors.append(f"{len(created) - len(existing_new)} created files not found")
    all_files = list(dict.fromkeys(existing_mod + existing_new))
    committed = _commit_implementation(ws_path, all_files)
    return FeatureWriterResult(
        files_modified=tuple(existing_mod), files_created=tuple(existing_new),
        implementation_summary=summary, committed=committed,
        raw_output=raw, errors=tuple(errors),
    )
