"""Scenario test generation — generate end-to-end tests from PR diffs.

Reads a PR diff, understands what changed, and generates scenario tests that
exercise the user-visible behavior changes. Tests are written using the
detected framework idioms and stored in the crucible workspace.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dark_factory.crucible.framework_detect import FrameworkProfile

logger = logging.getLogger(__name__)

# Limits
_MAX_TEST_FILES = 5
_MAX_LINES = 500
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


# ── Diff Analysis ───────────────────────────────────────────────


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


def _classify_changes(files: list[str]) -> dict[str, list[str]]:
    """Classify changed files by type."""
    categories: dict[str, list[str]] = {
        "routes": [], "components": [], "api": [],
        "models": [], "config": [], "tests": [], "other": [],
    }
    for f in files:
        fl = f.lower()
        if any(p in fl for p in ("route", "controller", "handler", "endpoint")):
            categories["routes"].append(f)
        elif any(p in fl for p in ("component", "page", "view", "template")):
            categories["components"].append(f)
        elif any(p in fl for p in ("api/", "service", "client")):
            categories["api"].append(f)
        elif any(p in fl for p in ("model", "schema", "entity", "migration")):
            categories["models"].append(f)
        elif any(p in fl for p in ("config", ".env", "setting")):
            categories["config"].append(f)
        elif any(p in fl for p in ("test", "spec", "__test__")):
            categories["tests"].append(f)
        else:
            categories["other"].append(f)
    return {k: v for k, v in categories.items() if v}


# ── Prompt Building ─────────────────────────────────────────────


def _build_prompt(
    pr_diff: str,
    pr_number: int,
    pr_title: str,
    frameworks: tuple[FrameworkProfile, ...],
    app_structure: str,
    existing_tests: str,
) -> str:
    """Build the scenario generation prompt for Claude."""
    # Truncate large diffs
    diff_text = pr_diff[:_MAX_DIFF_CHARS]
    if len(pr_diff) > _MAX_DIFF_CHARS:
        diff_text += f"\n\n[... truncated, {len(pr_diff) - _MAX_DIFF_CHARS} chars omitted ...]"

    fw_desc = "\n".join(
        f"- {fw.name} ({fw.language}): run with `{fw.run_cmd}`"
        for fw in frameworks
    )

    return f"""You are the Crucible Scenario Generator. Generate end-to-end scenario tests
for the changes in PR #{pr_number}: {pr_title}

## Test Frameworks Available
{fw_desc}

## App Structure
{app_structure}

## Existing Tests (for pattern matching)
{existing_tests}

## PR Diff
```diff
{diff_text}
```

## Rules
1. Each test must exercise a user-visible behavior changed by the PR
2. Tests must be self-contained (setup, act, assert, teardown)
3. Use the detected framework idioms and patterns
4. Test names must clearly describe the scenario
5. Do NOT test internal implementation details
6. Tests must be deterministic (no timing-dependent assertions without waits)
7. Generate 1-{_MAX_TEST_FILES} test files, each with 2-8 test cases
8. Total test code must not exceed {_MAX_LINES} lines

## File Naming
PR-specific: tests/pr-{pr_number}-<feature-slug>.spec.<ext>

## Output Format
For each test file, output between markers:
<<<SCENARIO_TEST file="tests/pr-{pr_number}-<name>.spec.<ext>">>>
... test code ...
<<<END_SCENARIO_TEST>>>
"""


def _parse_agent_response(response: str, pr_number: int) -> list[ScenarioTest]:
    """Parse scenario tests from agent response."""
    tests: list[ScenarioTest] = []
    pattern = re.compile(
        r'<<<SCENARIO_TEST\s+file="([^"]+)">>>\s*\n(.*?)\n<<<END_SCENARIO_TEST>>>',
        re.S,
    )
    for m in pattern.finditer(response):
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


# ── Public API ──────────────────────────────────────────────────


def generate_scenarios(
    workspace_path: str | Path,
    crucible_path: str | Path,
    pr_number: int,
    pr_diff: str,
    frameworks: tuple[FrameworkProfile, ...],
    *,
    pr_title: str = "",
    agent_fn: object | None = None,
) -> ScenarioGenResult:
    """Generate scenario tests from a PR diff.

    This function is agent-backed — it builds a prompt for Claude containing
    the PR diff, detected frameworks, and the app structure, then parses
    the generated tests from the response.

    Args:
        workspace_path: App workspace path.
        crucible_path: Crucible test repo path.
        pr_number: PR number.
        pr_diff: Full unified diff.
        frameworks: Detected framework profiles.
        pr_title: PR title for context.
        agent_fn: Optional callable for AI generation (for testing).
    """
    root = Path(workspace_path)
    cruc = Path(crucible_path)

    if not pr_diff.strip():
        return ScenarioGenResult(
            tests=(), pr_number=pr_number,
            pr_diff_summary="No diff provided",
            frameworks_used=(), error="Empty PR diff",
        )

    diff_summary = _summarize_diff(pr_diff)
    changed_files = _extract_changed_files(pr_diff)

    # Build app structure description
    app_structure = ""
    try:
        from dark_factory.setup.project_analyzer import analyze_project  # noqa: PLC0415
        analysis = analyze_project(str(root))
        app_structure = (
            f"Language: {analysis.language}, Framework: {analysis.framework}\n"
            f"Web server: {analysis.has_web_server}, Database: {analysis.has_database}\n"
            f"Source dirs: {', '.join(analysis.source_dirs)}\n"
            f"Test dirs: {', '.join(analysis.test_dirs)}"
        )
    except Exception:  # noqa: BLE001
        app_structure = "Unable to analyze project structure"

    # Scan existing test patterns in crucible
    existing_tests = ""
    tests_dir = cruc / "tests"
    if tests_dir.is_dir():
        test_files = list(tests_dir.glob("*.spec.*")) + list(tests_dir.glob("*.test.*"))
        if test_files:
            # Read first existing test for pattern reference
            sample = test_files[0]
            try:
                content = sample.read_text(encoding="utf-8")[:1000]
                existing_tests = f"Sample test ({sample.name}):\n{content}"
            except OSError:
                pass

    prompt = _build_prompt(
        pr_diff, pr_number, pr_title or f"PR #{pr_number}",
        frameworks, app_structure, existing_tests,
    )

    # Generate tests via agent
    if agent_fn is not None:
        try:
            response = str(agent_fn(prompt))
        except Exception as exc:  # noqa: BLE001
            return ScenarioGenResult(
                tests=(), pr_number=pr_number,
                pr_diff_summary=diff_summary,
                frameworks_used=tuple(fw.name for fw in frameworks),
                error=f"Agent error: {exc}",
            )
    else:
        # When no agent_fn provided, generate a minimal smoke test
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


def _placeholder_fw() -> FrameworkProfile:
    from dark_factory.crucible.framework_detect import FrameworkProfile  # noqa: PLC0415
    return FrameworkProfile(
        name="playwright", language="TypeScript",
        install_cmd="npm install @playwright/test",
        run_cmd="npx playwright test",
        config_file="playwright.config.ts",
        reporter_json="--reporter=json",
    )


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
            f"import pytest\n\n\n"
            f"def test_pr_{pr_number}_smoke():\n"
            f'    """Verify basic functionality after PR #{pr_number}."""\n'
            f"    # TODO: implement scenario test\n"
            f"    assert True\n"
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
