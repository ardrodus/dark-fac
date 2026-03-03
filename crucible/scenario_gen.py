"""Scenario test generation utilities for Crucible."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.crucible.framework_detect import FrameworkProfile

logger = logging.getLogger(__name__)

# Limits
_MAX_TEST_FILES = 5
_MAX_DIFF_CHARS = 50_000  # truncate large diffs


@dataclass(frozen=True, slots=True)
class ScenarioTest:
    """A single generated scenario test."""

    name: str  # test name/title
    file_path: str  # relative path in crucible repo
    test_code: str  # actual test source code
    framework: str  # which framework this test uses
    category: str  # "smoke" | "regression" | "e2e"
    pr_files_covered: tuple[str, ...] = ()  # which PR files this tests


@dataclass(frozen=True, slots=True)
class ScenarioGenResult:
    """Result of scenario generation."""

    tests: tuple[ScenarioTest, ...]
    pr_number: int
    pr_diff_summary: str
    frameworks_used: tuple[str, ...]
    error: str = ""


# ── Diff Analysis (deterministic — no AI needed) ─────────────


def _summarize_diff(diff: str) -> str:
    """Extract a concise summary from a unified diff."""
    files_changed: list[str] = []
    additions = deletions = 0
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r"b/(.+)$", line)
            if m:
                files_changed.append(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return (
        f"Files changed: {len(files_changed)} "
        f"(+{additions}/-{deletions} lines)\n"
        f"Modified: {', '.join(files_changed[:20])}"
    )


def _extract_changed_files(diff: str) -> list[str]:
    """Extract file paths changed in the diff."""
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r"b/(.+)$", line)
            if m:
                files.append(m.group(1))
    return files


# ── Response Parsing (deterministic) ─────────────────────────

_SCENARIO_PATTERN = re.compile(
    r'<<<SCENARIO_TEST\s+file="([^"]+)">>>\s*\n(.*?)\n<<<END_SCENARIO_TEST>>>',
    re.S,
)


def _parse_agent_response(response: str, pr_number: int) -> list[ScenarioTest]:
    """Parse scenario tests from agent response."""
    tests: list[ScenarioTest] = []
    for m in _SCENARIO_PATTERN.finditer(response):
        file_path = m.group(1)
        code = m.group(2).strip()
        # Determine framework from file extension
        fw = "playwright"
        if file_path.endswith(".py"):
            fw = "pytest"
        elif file_path.endswith((".test.js", ".test.ts")):
            fw = "jest"
        # Extract test name from file path
        name = Path(file_path).stem.replace(f"pr-{pr_number}-", "")
        tests.append(ScenarioTest(
            name=name,
            file_path=file_path,
            test_code=code,
            framework=fw,
            category="smoke",
        ))
    return tests[:_MAX_TEST_FILES]


# ── Fallback (when no agent available) ───────────────────────


def _generate_fallback(
    pr_number: int,
    fw: FrameworkProfile,
    changed_files: list[str],
) -> str:
    """Generate a minimal smoke test when no agent is available."""
    slug = "smoke"
    if changed_files:
        # Derive slug from first changed file
        first = Path(changed_files[0]).stem
        slug = re.sub(r"[^a-zA-Z0-9]", "-", first).strip("-")[:30] or "smoke"

    if fw.language == "Python":
        ext = "py"
        code = (
            f'"""Smoke test for PR #{pr_number} changes."""\n'
            f"import httpx\nimport pytest\n\n\n"
            f"def test_pr_{pr_number}_smoke():\n"
            f'    """Verify the app responds after PR #{pr_number} changes."""\n'
            f'    # TODO: Replace with actual endpoint from the PR diff\n'
            f'    response = httpx.get("http://localhost:8000/health")\n'
            f"    assert response.status_code == 200\n"
        )
    else:
        ext = "spec.ts"
        code = (
            f"import {{ test, expect }} from '@playwright/test';\n\n"
            f"test.describe('PR #{pr_number} smoke tests', () => {{\n"
            f"  test('should load application after changes', async ({{ page }}) => {{\n"
            f"    await page.goto('/');\n"
            f"    await expect(page).toHaveTitle(/.+/);\n"
            f"  }});\n"
            f"}});\n"
        )

    return (
        f'<<<SCENARIO_TEST file="tests/pr-{pr_number}-{slug}.{ext}">>>\n'
        f"{code}\n"
        f"<<<END_SCENARIO_TEST>>>"
    )


def _placeholder_fw() -> FrameworkProfile:
    from dark_factory.crucible.framework_detect import FrameworkProfile  # noqa: PLC0415
    return FrameworkProfile(
        name="playwright", language="TypeScript",
        install_cmd="npm install @playwright/test",
        run_cmd="npx playwright test",
        config_file="playwright.config.ts",
        reporter_json="--reporter=json",
    )


# ── Public API ───────────────────────────────────────────────


def generate_scenarios(
    workspace_path: str | Path,
    crucible_path: str | Path,
    pr_number: int,
    pr_diff: str,
    frameworks: tuple[FrameworkProfile, ...],
    *,
    pr_title: str = "",
    agent_fn: Callable[[str], str] | None = None,
) -> ScenarioGenResult:
    """Generate scenario tests from a PR diff.

    When called via the pipeline engine, the agent runs in crucible.dot's
    generate_scenarios node.  The ``agent_fn`` receives the diff as input
    and returns the raw agent response with <<<SCENARIO_TEST>>> markers.

    When no ``agent_fn`` is provided, generates a minimal fallback smoke test.
    """
    if not pr_diff.strip():
        return ScenarioGenResult(
            tests=(), pr_number=pr_number,
            pr_diff_summary="No diff provided",
            frameworks_used=(), error="Empty PR diff",
        )

    diff_summary = _summarize_diff(pr_diff)
    changed_files = _extract_changed_files(pr_diff)

    # Generate tests via agent or fallback
    if agent_fn is not None:
        try:
            response = str(agent_fn(pr_diff))
        except Exception as exc:  # noqa: BLE001
            logger.error("Scenario generation agent failed: %s", exc)
            return ScenarioGenResult(
                tests=(), pr_number=pr_number,
                pr_diff_summary=diff_summary,
                frameworks_used=tuple(fw.name for fw in frameworks),
                error=f"Agent error: {exc}",
            )
    else:
        # No agent — generate minimal fallback smoke test
        fw = frameworks[0] if frameworks else _placeholder_fw()
        response = _generate_fallback(pr_number, fw, changed_files)

    tests = _parse_agent_response(response, pr_number)

    # Tag tests with covered files
    tagged: list[ScenarioTest] = []
    for t in tests:
        tagged.append(ScenarioTest(
            name=t.name, file_path=t.file_path, test_code=t.test_code,
            framework=t.framework, category=t.category,
            pr_files_covered=tuple(changed_files),
        ))

    fw_used = tuple(sorted({t.framework for t in tagged}))
    logger.info(
        "Generated %d scenario tests for PR #%d (%s)",
        len(tagged), pr_number, ", ".join(fw_used),
    )

    return ScenarioGenResult(
        tests=tuple(tagged),
        pr_number=pr_number,
        pr_diff_summary=diff_summary,
        frameworks_used=fw_used,
    )


def write_scenarios(
    crucible_path: str | Path,
    result: ScenarioGenResult,
) -> list[str]:
    """Write generated test files to the crucible workspace.

    Returns list of written file paths (relative to crucible_path).
    """
    cruc = Path(crucible_path)
    written: list[str] = []

    for test in result.tests:
        target = cruc / test.file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(test.test_code + "\n", encoding="utf-8")
            written.append(test.file_path)
            logger.info("Wrote scenario test: %s", test.file_path)
        except OSError as exc:
            logger.error("Failed to write %s: %s", test.file_path, exc)

    return written
