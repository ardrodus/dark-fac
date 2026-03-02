"""TDD Test Writer — generate test files from specs without seeing implementation.

Ports Stage 1 of tdd-pipeline.sh.  The agent prompt includes PRD, design doc,
test strategy, interface definitions, and test patterns but **excludes**
implementation source code (information gap principle).
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

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 300
_FW_RE = re.compile(r"(pytest|unittest|jest|mocha|vitest|rspec|go\s*test)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SpecBundle:
    """Design artifacts for the Test Writer.  No implementation source (information gap)."""

    prd: str = ""
    design_doc: str = ""
    test_strategy: str = ""
    interface_definitions: str = ""
    test_patterns: str = ""
    api_contract: str = ""
    schema_spec: str = ""


@dataclass(frozen=True, slots=True)
class TestWriterResult:
    """Outcome of a Test Writer invocation."""

    test_files_created: tuple[str, ...]
    test_count: int
    framework_used: str
    committed: bool = False
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _build_prompt(specs: SpecBundle, workspace_path: str) -> str:
    """Assemble prompt from *specs*.  Never includes implementation source."""
    parts: list[str] = [
        "You are the Test Writer agent in a TDD pipeline.",
        "Write **failing** tests covering every acceptance criterion.",
        "Do NOT create or modify implementation source files.\n",
        "INFORMATION GAP PRINCIPLE: You have NOT been given any implementation",
        "source code. Test against the specified interfaces only.\n",
    ]
    _artifact_sections = [
        ("prd", "## PRD / User Story"),
        ("design_doc", "## Technical Design Document"),
        ("test_strategy", "## Test Strategy"),
        ("api_contract", "## API Contract"),
        ("schema_spec", "## Schema Specification"),
        ("interface_definitions", "## Interface Definitions"),
        ("test_patterns", "## Existing Test Patterns"),
    ]
    for attr, heading in _artifact_sections:
        content = getattr(specs, attr)
        if content:
            parts.append(f"{heading}\n\n{content}\n")
    parts.extend([
        "## Output Format\n",
        "Output a JSON object: "
        '{"test_files_created": [...], "test_count": N, "framework_used": "..."}',
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


def _parse_result(raw: str) -> tuple[list[str], int, str]:
    """Extract structured fields from agent JSON output."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{[^{}]*\"test_files_created\"[^{}]*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    data: dict[str, object] = json.loads(text)
    raw_files = data.get("test_files_created", [])
    files = [str(f) for f in raw_files] if isinstance(raw_files, list) else []
    raw_count = data.get("test_count", len(files))
    count = int(raw_count) if isinstance(raw_count, (int, float)) else len(files)
    raw_fw = str(data.get("framework_used", ""))
    framework = raw_fw if raw_fw else _detect_framework(raw)
    return files, count, framework


def _detect_framework(text: str) -> str:
    match = _FW_RE.search(text)
    return match.group(1).lower() if match else "unknown"


def _commit_tests(workspace_path: str, test_files: list[str]) -> bool:
    """Stage and commit generated test files to the workspace branch."""
    if not test_files:
        return False
    from factory.integrations.shell import git  # noqa: PLC0415

    for tf in test_files:
        git(["add", tf], cwd=workspace_path, check=False)
    if not git(["diff", "--cached", "--name-only"], cwd=workspace_path).stdout.strip():
        return False
    result = git(
        ["commit", "-m", "test: add TDD test files (Test Writer agent)"],
        cwd=workspace_path, check=False,
    )
    if result.returncode != 0:
        logger.warning("git commit failed: %s", result.stderr.strip())
        return False
    logger.info("Committed test files to workspace branch")
    return True


def run_test_writer(
    specs: SpecBundle,
    workspace: Workspace,
    *,
    invoke_fn: Callable[[str], str] | None = None,
) -> TestWriterResult:
    """Generate test files from *specs* in *workspace* via Claude agent.

    Prompt includes PRD, design doc, test strategy, interface definitions,
    and test patterns.  Excludes implementation source (information gap).
    """
    ws_path = workspace.path
    prompt = _build_prompt(specs, ws_path)
    errors: list[str] = []

    try:
        raw = _invoke_agent(prompt, ws_path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Test Writer agent failed: %s", exc)
        return TestWriterResult(
            test_files_created=(), test_count=0, framework_used="unknown",
            raw_output="", errors=(str(exc),),
        )

    try:
        files, count, framework = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Failed to parse Test Writer output: %s", exc)
        files, count, framework = [], 0, _detect_framework(raw)
        errors.append(f"parse error: {exc}")

    existing = [f for f in files if Path(ws_path, f).exists()]
    if len(existing) < len(files):
        errors.append(f"{len(files) - len(existing)} reported files not found on disk")

    committed = _commit_tests(ws_path, existing)
    return TestWriterResult(
        test_files_created=tuple(existing), test_count=count,
        framework_used=framework, committed=committed,
        raw_output=raw, errors=tuple(errors),
    )
