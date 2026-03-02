"""Integration test gate — cross-story interaction validation.

Port of integration-test-gate.sh.  Runs AFTER individual TDD cycles
complete and BEFORE PR creation.  Collects story artifacts, invokes
Claude to write integration tests, and runs a fix cycle if tests fail.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import builtins
    from collections.abc import Callable

    from factory.workspace.manager import Workspace

from factory.pipeline.parallel_stories import StoryResult, StoryStatus

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 300
_MAX_FIX_CYCLES = 2
_RESULT_RE = re.compile(r"INTEGRATION_RESULT:\s*(\S+)", re.IGNORECASE)
_SRC_EXTS = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb"})
_TEST_EXTS = frozenset({".py", ".ts", ".js", ".go", ".rs", ".java", ".rb", ".sh"})


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of the integration test gate."""

    passed: bool
    summary: str = ""
    test_files_created: tuple[str, ...] = ()
    fix_cycles_used: int = 0
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _collect_artifacts(
    workspace_path: str, stories: builtins.list[StoryResult],
) -> str:
    ws = Path(workspace_path)
    src = ws / "src" if (ws / "src").is_dir() else ws
    files = sorted(
        str(fp.relative_to(ws))
        for fp in src.rglob("*") if fp.suffix in _SRC_EXTS and fp.is_file()
    )
    ids = [s.story_id for s in stories if s.status == StoryStatus.COMPLETED]
    parts: builtins.list[str] = []
    if files:
        parts.append("### Implementation Files\n" + "\n".join(f"- {f}" for f in files))
    if ids:
        parts.append(f"### Completed Stories\n{', '.join(ids)}")
    return "\n\n".join(parts)


def _collect_tests(workspace_path: str) -> str:
    tests_dir = Path(workspace_path) / "tests"
    if not tests_dir.is_dir():
        return "No tests/ directory found."
    ws = Path(workspace_path)
    files = sorted(
        str(fp.relative_to(ws))
        for fp in tests_dir.rglob("*") if fp.suffix in _TEST_EXTS and fp.is_file()
    )
    return "\n".join(files) if files else "No test files found in tests/."


def _integration_files(workspace_path: str) -> builtins.list[str]:
    d = Path(workspace_path) / "tests" / "integration"
    if not d.is_dir():
        return []
    ws = Path(workspace_path)
    return sorted(
        str(fp.relative_to(ws))
        for fp in d.rglob("*") if fp.suffix in _TEST_EXTS and fp.is_file()
    )


def _build_prompt(
    workspace_path: str, stories: builtins.list[StoryResult],
) -> str:
    return "\n".join([
        "You are the Integration Tester agent.",
        "Write cross-story integration tests in `tests/integration/`.\n",
        "## Story Artifacts\n", _collect_artifacts(workspace_path, stories),
        "\n## Existing Tests (do NOT duplicate)\n```",
        _collect_tests(workspace_path), "```\n",
        "## Instructions",
        "1. Create `tests/integration/` if needed.",
        "2. Write tests verifying cross-story interactions:",
        "   - Story A output works as Story B input",
        "   - Full flows traverse all layers",
        "   - Errors propagate across module boundaries",
        "3. Run tests to verify they pass.",
        "4. Do NOT modify existing `src/` or `tests/` files.\n",
        "End output with: `INTEGRATION_RESULT: passed` or `INTEGRATION_RESULT: failed`",
    ])


def _build_fix_prompt(failure_summary: str) -> str:
    return "\n".join([
        "You are the Feature Writer. Integration tests failed.\n",
        "## Failures\n", failure_summary,
        "\n## Instructions",
        "- Fix code in `src/` only (do NOT touch `tests/`).",
        "- Focus on cross-story boundary issues.",
        "- Verify individual story tests still pass.",
    ])


def _invoke_agent(
    prompt: str, workspace_path: str,
    *, invoke_fn: Callable[[str], str] | None = None,
) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415

    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, check=True, cwd=workspace_path,
    ).stdout


def _parse_verdict(raw: str) -> str:
    match = _RESULT_RE.findall(raw)
    return str(match[-1]).lower() if match else ""


def run_integration_test_gate(
    workspace: Workspace,
    stories: builtins.list[StoryResult],
    *,
    invoke_fn: Callable[[str], str] | None = None,
) -> GateResult:
    """Run the integration test gate after TDD cycles complete.

    1. Collect artifacts from completed stories.
    2. Generate integration test prompt covering cross-story interactions.
    3. Invoke Claude to write and run integration tests.
    4. Fix cycle: if tests fail, feed errors back to Feature Writer (max 2).
    """
    ws_path = workspace.path
    if not any(s.status == StoryStatus.COMPLETED for s in stories):
        return GateResult(passed=True, summary="No completed stories -- skipped.")

    prompt = _build_prompt(ws_path, stories)
    errors: builtins.list[str] = []
    try:
        raw = _invoke_agent(prompt, ws_path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Integration tester agent failed: %s", exc)
        return GateResult(passed=False, summary="Agent invocation failed.", errors=(str(exc),))

    verdict = _parse_verdict(raw)
    if verdict == "passed":
        tf = _integration_files(ws_path)
        return GateResult(
            passed=True, summary=f"Integration tests passed ({len(tf)} files).",
            test_files_created=tuple(tf), raw_output=raw,
        )
    if verdict != "failed":
        errors.append("Could not parse verdict from agent output.")

    fix_cycles = 0
    for cycle in range(1, _MAX_FIX_CYCLES + 1):
        fix_cycles = cycle
        logger.info("Integration fix cycle %d/%d", cycle, _MAX_FIX_CYCLES)
        summary = raw[-2000:] if len(raw) > 2000 else raw  # noqa: PLR2004
        try:
            _invoke_agent(_build_fix_prompt(summary), ws_path, invoke_fn=invoke_fn)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"fix cycle {cycle}: {exc}")
            continue
        try:
            raw = _invoke_agent(_build_prompt(ws_path, stories), ws_path, invoke_fn=invoke_fn)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"re-run {cycle}: {exc}")
            continue
        verdict = _parse_verdict(raw)
        if verdict == "passed":
            tf = _integration_files(ws_path)
            return GateResult(
                passed=True, summary=f"Passed after {cycle} fix cycle(s).",
                test_files_created=tuple(tf), fix_cycles_used=cycle, raw_output=raw,
            )

    tf = _integration_files(ws_path)
    return GateResult(
        passed=False, summary=f"Failed after {fix_cycles} fix cycle(s).",
        test_files_created=tuple(tf), fix_cycles_used=fix_cycles,
        raw_output=raw, errors=tuple(errors),
    )
