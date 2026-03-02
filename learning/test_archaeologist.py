"""Test Archaeologist agent — discovers test framework, patterns, fixtures, coverage, CI integration."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.learning.scout import ScoutResult
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT, _STATE_DIR = 300, Path(".dark-factory")
_EXCLUDE = frozenset(
    "node_modules .git vendor dist __pycache__ .tox .venv .mypy_cache coverage build "
    "target .next .nuxt venv env .dark-factory".split())
_TEST_DIRS = ("tests", "test", "spec", "__tests__", "e2e", "integration")
_TEST_CONFIGS = (
    "jest.config.js", "jest.config.ts", "vitest.config.ts", "vitest.config.js",
    ".mocharc.yml", "karma.conf.js", "cypress.config.ts", "playwright.config.ts",
    "pytest.ini", "conftest.py", ".coveragerc", "tox.ini",
    "phpunit.xml", ".rspec", "spec_helper.rb",
)
_TEST_FILE_RE = re.compile(
    r"\.test\.[jt]sx?$|\.spec\.[jt]sx?$|_test\.go$|_test\.py$|test_.*\.py$"
    r"|Test\.java$|Tests?\.cs$|_spec\.rb$|_test\.rs$|Test\.kt$", re.IGNORECASE)
_FIXTURE_KW = ("fixture", "factory", "testdata", "test_data", "seed", "mock", "stub", "fake")
_CI_CONFIGS = (
    ".github/workflows", ".gitlab-ci.yml", "Jenkinsfile",
    ".circleci/config.yml", "azure-pipelines.yml",
)


@dataclass(frozen=True, slots=True)
class TestArchResult:
    """Structured output of the Test Archaeologist agent."""
    test_framework: str = ""
    test_patterns: tuple[str, ...] = ()
    fixtures: tuple[str, ...] = ()
    mocks: tuple[str, ...] = ()
    coverage: str = ""
    ci_integration: str = ""
    raw_output: str = ""
    errors: tuple[str, ...] = ()


def _rd(p: Path, limit: int = 6000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""

def _strs(data: dict[str, object], key: str) -> tuple[str, ...]:
    raw = data.get(key)
    return tuple(str(e) for e in raw) if isinstance(raw, list) else ()

def _collect_context(ws: Path, scout: ScoutResult) -> str:
    parts: list[str] = [f"## Scout Overview\n{scout.app_overview}\n"]
    if scout.build_system:
        parts.append(f"Build system: {scout.build_system}\n")
    if scout.config_files:
        parts.append(f"Config files: {', '.join(scout.config_files)}\n")
    # Test configuration files
    for name in _TEST_CONFIGS:
        content = _rd(ws / name)
        if content:
            parts.append(f"## {name}\n{content}\n")
    # Scan test directories
    seen: set[Path] = set()
    for dname in _TEST_DIRS:
        candidates = [ws / dname] + [ws / s / dname for s in ("src", "lib", "app")]
        dd = next((d for d in candidates if d.is_dir()), None)
        if dd is None:
            continue
        for f in sorted(dd.rglob("*"))[:20]:
            if f.is_file() and f not in seen:
                seen.add(f)
                parts.append(f"## {f.relative_to(ws)}\n{_rd(f)}\n")
    # Find test files by naming convention and fixture/mock files in one pass
    for f in ws.rglob("*"):
        if not f.is_file() or f in seen or any(p in _EXCLUDE for p in f.parts):
            continue
        is_test = _TEST_FILE_RE.search(f.name) and len(seen) < 30  # noqa: PLR2004
        is_fixture = any(kw in f.stem.lower() for kw in _FIXTURE_KW) and len(seen) < 35  # noqa: PLR2004
        if is_test or is_fixture:
            seen.add(f)
            parts.append(f"## {f.relative_to(ws)}\n{_rd(f)}\n")
    # CI configuration
    for ci in _CI_CONFIGS:
        ci_path = ws / ci
        if ci_path.is_dir():
            for f in sorted(ci_path.iterdir())[:5]:
                if f.is_file():
                    parts.append(f"## {f.relative_to(ws)}\n{_rd(f, 3000)}\n")
        elif ci_path.is_file():
            parts.append(f"## {ci}\n{_rd(ci_path, 3000)}\n")
    # Package manifest for test deps
    for name in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod"):
        content = _rd(ws / name)
        if content:
            parts.append(f"## {name}\n{content}\n")
    return "\n".join(parts)

def _build_prompt(context: str) -> str:
    return f"""\
You are the Test Archaeologist Agent. Analyze the workspace data below and produce a JSON object \
documenting the test infrastructure found.

{context}

## Output Format (strict JSON)
{{"test_framework": "pytest 7.x with pytest-cov, pytest-asyncio",\
 "test_patterns": ["pytest function-based tests with fixtures", ...],\
 "fixtures": ["conftest.py shared fixtures", "tests/fixtures/ JSON data files", ...],\
 "mocks": ["unittest.mock.patch for external APIs", ...],\
 "coverage": "pytest-cov with 80% threshold, lcov output",\
 "ci_integration": "GitHub Actions runs pytest on push, coverage uploaded to Codecov"}}

Guidelines:
- test_framework: frameworks, runners, and versions detected
- test_patterns: testing styles, organization, assertion patterns
- fixtures: test data files, factory functions, shared setup
- mocks: mock/stub libraries and patterns used
- coverage: coverage tools, thresholds, reporting
- ci_integration: how tests run in CI/CD
Output ONLY the JSON object, no markdown fences."""

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

def _parse_result(raw: str) -> TestArchResult:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\"test_framework\".*\}", text, re.DOTALL)
    if not match:
        match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return TestArchResult(raw_output=raw, errors=("no JSON found in output",))
    data: dict[str, object] = json.loads(match.group(0))
    return TestArchResult(
        test_framework=str(data.get("test_framework", "")),
        test_patterns=_strs(data, "test_patterns"),
        fixtures=_strs(data, "fixtures"),
        mocks=_strs(data, "mocks"),
        coverage=str(data.get("coverage", "")),
        ci_integration=str(data.get("ci_integration", "")),
        raw_output=raw,
    )

def _save(result: TestArchResult, repo_name: str, *, state_dir: Path | None = None) -> Path:
    sd = (state_dir or _STATE_DIR) / "learning" / repo_name
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "test_archaeologist.json"
    payload = {k: v for k, v in asdict(result).items() if k != "raw_output"}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Test Archaeologist results saved to %s", out)
    return out

def run_test_archaeologist(
    workspace: Workspace, scout: ScoutResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> TestArchResult:
    """Discover test framework, patterns, fixtures, mocks, coverage, and CI integration."""
    ws_path = Path(workspace.path)
    repo_name = workspace.name or ws_path.name
    context = _collect_context(ws_path, scout)
    prompt = _build_prompt(context)
    try:
        raw = _invoke_agent(prompt, workspace.path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Test Archaeologist agent failed: %s", exc)
        return TestArchResult(raw_output="", errors=(str(exc),))
    try:
        result = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Test Archaeologist parse error: %s", exc)
        return TestArchResult(raw_output=raw, errors=(f"parse error: {exc}",))
    _save(result, repo_name, state_dir=state_dir)
    return result
