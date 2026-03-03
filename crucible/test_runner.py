"""Unified test runner — multi-framework, two-mode, failure-classifying.

Runs tests inside the containerized app, supporting multiple frameworks,
smoke/full modes, and deterministic failure classification.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dark_factory.crucible.orchestrator import (
        CrucibleConfig,
        CrucibleVerdict,
        PhaseMetrics,
        TestResult,
    )
    from dark_factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)


class TestMode(Enum):
    SMOKE = "smoke"  # Round 1: new PR tests only
    FULL = "full"  # Round 2: all existing + new tests
    REGRESSION = "regression"  # alias for FULL


class FailureClass(Enum):
    REAL_BUG = "real_bug"
    FLAKY = "flaky"
    ENV_ISSUE = "env_issue"
    NEEDS_LIVE = "needs_live"


@dataclass(frozen=True, slots=True)
class ClassifiedFailure:
    """A test failure with its classification."""

    test_name: str
    classification: FailureClass
    error_message: str
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class RunResult:
    """Result from a single test run (one round)."""

    mode: TestMode
    verdict: str  # CrucibleVerdict value string
    pass_count: int
    fail_count: int
    skip_count: int
    duration_s: float
    test_results: tuple[Any, ...] = ()
    failures: tuple[ClassifiedFailure, ...] = ()
    logs: str = ""
    screenshots: tuple[str, ...] = ()
    error: str = ""


# ── Failure Classification ──────────────────────────────────────

_REAL_BUG_PATTERNS = re.compile(
    r"AssertionError|assert\s|TypeError|ReferenceError|NameError"
    r"|AttributeError|KeyError|IndexError|ValueError"
    r"|HTTP\s+[45]\d\d|status\s*(?:code\s*)?(?:=\s*)?[45]\d\d"
    r"|Expected.*(?:to\s+be|to\s+equal|to\s+match|to\s+have)"
    r"|not\s+equal|mismatch",
    re.I,
)

_FLAKY_PATTERNS = re.compile(
    r"timeout|timed?\s*out|ETIMEDOUT|ECONNRESET"
    r"|element\s+not\s+found|no\s+such\s+element"
    r"|detached\s+from\s+DOM|stale\s+element"
    r"|retry|flak|intermittent|race\s+condition"
    r"|waiting\s+for\s+selector",
    re.I,
)

_ENV_PATTERNS = re.compile(
    r"ECONNREFUSED|connection\s+refused|ENOTFOUND"
    r"|DNS\s+resolution|cannot\s+resolve|port\s+not\s+bound"
    r"|address\s+already\s+in\s+use|EADDRINUSE"
    r"|permission\s+denied|EACCES|no\s+such\s+file",
    re.I,
)

_NEEDS_LIVE_PATTERNS = re.compile(
    r"skip.*live|requires?\s+live|external\s+service"
    r"|\.amazonaws\.com|\.azure\.|\.googleapis\."
    r"|production\s+only|staging\s+only",
    re.I,
)


def classify_failure(test_name: str, error: str, logs: str = "") -> FailureClass:
    """Classify a test failure using deterministic heuristics.

    Priority order: NEEDS_LIVE > ENV_ISSUE > FLAKY > REAL_BUG (default).
    """
    combined = f"{error}\n{logs}"

    if _NEEDS_LIVE_PATTERNS.search(combined):
        return FailureClass.NEEDS_LIVE
    if _ENV_PATTERNS.search(combined):
        return FailureClass.ENV_ISSUE
    if _FLAKY_PATTERNS.search(combined):
        return FailureClass.FLAKY
    # Default: if it has assertion-like errors, it's a real bug
    return FailureClass.REAL_BUG


# ── Test Execution ──────────────────────────────────────────────


def _build_run_command(
    framework: str,
    test_files: list[str] | None = None,
    reporter_json: str = "",
) -> str:
    """Build the test execution command for a given framework."""
    cmds: dict[str, str] = {
        "playwright": "npx playwright test",
        "cypress": "npx cypress run",
        "jest": "npx jest",
        "supertest": "npx jest --testPathPattern=api",
        "pytest": "pytest",
        "httpx": "pytest",
    }
    base = cmds.get(framework, "npx playwright test")
    if reporter_json:
        base = f"{base} {reporter_json}"
    elif framework == "playwright":
        base = f"{base} --reporter=json"
    elif framework == "pytest":
        base = f"{base} --json-report --json-report-file=report.json -v"
    elif framework in ("jest", "supertest"):
        base = f"{base} --json --outputFile=report.json"

    if test_files:
        base = f"{base} {' '.join(test_files)}"

    return base


def _parse_json_results(raw: str, framework: str) -> tuple[list[Any], int, int, int]:
    """Parse test results from JSON output, framework-aware."""
    from dark_factory.crucible.orchestrator import TestResult  # noqa: PLC0415

    results: list[TestResult] = []
    pc = fc = sc = 0

    try:
        data: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return results, pc, fc, sc

    if not isinstance(data, dict):
        return results, pc, fc, sc

    if framework in ("playwright",):
        # Playwright JSON reporter format
        for suite in data.get("suites", []):
            if not isinstance(suite, dict):
                continue
            for spec in suite.get("specs", []):
                if not isinstance(spec, dict):
                    continue
                name = str(spec.get("title", "unknown"))
                ok = spec.get("ok", False)
                status = "pass" if ok else "fail"
                dur = 0.0
                for t in spec.get("tests", []):
                    if not isinstance(t, dict):
                        continue
                    rl = t.get("results", [])
                    dur += float(rl[0].get("duration", 0)) if rl else 0.0
                    st = str(t.get("status", ""))
                    if st == "skipped":
                        status = "skip"
                    elif st in ("unexpected", "flaky"):
                        status = "fail"
                results.append(TestResult(name=name, status=status, duration_ms=dur))
                if status == "pass":
                    pc += 1
                elif status == "fail":
                    fc += 1
                else:
                    sc += 1

    elif framework in ("jest", "supertest"):
        # Jest JSON format
        for result in data.get("testResults", []):
            if not isinstance(result, dict):
                continue
            for ar in result.get("assertionResults", []):
                if not isinstance(ar, dict):
                    continue
                name = str(ar.get("fullName", ar.get("title", "unknown")))
                status = str(ar.get("status", ""))
                dur = float(ar.get("duration", 0))
                mapped = "pass" if status == "passed" else ("skip" if status == "pending" else "fail")
                results.append(TestResult(name=name, status=mapped, duration_ms=dur))
                if mapped == "pass":
                    pc += 1
                elif mapped == "fail":
                    fc += 1
                else:
                    sc += 1

    elif framework in ("pytest", "httpx"):
        # pytest-json-report format
        for name, info in data.get("tests", {}).items():
            if not isinstance(info, dict):
                continue
            outcome = str(info.get("outcome", ""))
            dur = float(info.get("duration", 0)) * 1000  # seconds to ms
            mapped = "pass" if outcome == "passed" else ("skip" if outcome == "skipped" else "fail")
            results.append(TestResult(name=str(name), status=mapped, duration_ms=dur))
            if mapped == "pass":
                pc += 1
            elif mapped == "fail":
                fc += 1
            else:
                sc += 1

    return results, pc, fc, sc


def _determine_verdict(
    pc: int, fc: int, sc: int,
    failures: list[ClassifiedFailure],
) -> str:
    """Determine verdict from test counts and classified failures."""
    from dark_factory.crucible.orchestrator import CrucibleVerdict  # noqa: PLC0415

    # Any real bug -> NO_GO
    if any(f.classification == FailureClass.REAL_BUG for f in failures):
        return CrucibleVerdict.NO_GO.value
    # Any needs-live -> NEEDS_LIVE
    if any(f.classification == FailureClass.NEEDS_LIVE for f in failures):
        return CrucibleVerdict.NEEDS_LIVE.value
    # Hard failures without classification
    if fc > 0:
        return CrucibleVerdict.NO_GO.value
    # All skips, no passes
    if sc > 0 and pc == 0:
        return CrucibleVerdict.NEEDS_LIVE.value
    return CrucibleVerdict.GO.value


# ── Public API ──────────────────────────────────────────────────


def run_tests(
    workspace: Workspace,
    crucible_path: str | Path,
    config: Any,
    *,
    mode: TestMode = TestMode.FULL,
    pr_number: int = 0,
    test_files: list[str] | None = None,
    framework: str = "",
    docker_fn: Any = None,
) -> RunResult:
    """Execute tests inside the containerized app.

    SMOKE mode: runs only the files in *test_files* (newly generated PR tests).
    FULL mode: runs all tests in the crucible test directory.

    Args:
        workspace: Application workspace.
        crucible_path: Path to crucible test repo.
        config: CrucibleConfig instance.
        mode: SMOKE (round 1) or FULL (round 2).
        pr_number: PR number for labeling.
        test_files: Specific files for SMOKE mode.
        framework: Override auto-detected framework.
        docker_fn: Optional Docker command callable.
    """
    from dark_factory.crucible.orchestrator import CrucibleVerdict  # noqa: PLC0415

    t0 = time.monotonic()
    cruc = Path(crucible_path)
    fw = framework or _detect_framework_from_crucible(cruc)

    # Build command
    files_to_run: list[str] | None = None
    if mode == TestMode.SMOKE and test_files:
        files_to_run = test_files
    run_cmd = _build_run_command(fw, files_to_run)

    # Execute tests in container
    ctr = f"{config.project_name}-app-1"
    dk = docker_fn or _default_docker
    try:
        r = dk(
            ["exec", ctr, "sh", "-c",
             f"cd /workspace && {run_cmd} 2>&1 || true"],
            timeout=float(config.test_timeout),
            cwd=workspace.path,
        )
        test_output = r.stdout
    except Exception as exc:  # noqa: BLE001
        return RunResult(
            mode=mode, verdict=CrucibleVerdict.NO_GO.value,
            pass_count=0, fail_count=0, skip_count=0,
            duration_s=time.monotonic() - t0,
            error=f"Docker exec failed: {exc}",
        )

    # Parse results
    test_results, pc, fc, sc = _parse_json_results(test_output, fw)

    # Classify failures
    classified: list[ClassifiedFailure] = []
    for tr in test_results:
        if tr.status == "fail":
            cls = classify_failure(tr.name, test_output)
            classified.append(ClassifiedFailure(
                test_name=tr.name,
                classification=cls,
                error_message=f"Failed in {mode.value} round",
            ))

    verdict = _determine_verdict(pc, fc, sc, classified)
    elapsed = time.monotonic() - t0

    logger.info(
        "Crucible %s round: %s (pass=%d fail=%d skip=%d %.1fs) [%s]",
        mode.value, verdict, pc, fc, sc, elapsed, fw,
    )

    return RunResult(
        mode=mode,
        verdict=verdict,
        pass_count=pc,
        fail_count=fc,
        skip_count=sc,
        duration_s=elapsed,
        test_results=tuple(test_results),
        failures=tuple(classified),
        logs=test_output,
    )


def _detect_framework_from_crucible(cruc: Path) -> str:
    """Auto-detect framework from crucible repo contents."""
    try:
        pkg = (cruc / "package.json").read_text(encoding="utf-8")
    except OSError:
        pkg = ""
    if "@playwright/test" in pkg:
        return "playwright"
    if "cypress" in pkg:
        return "cypress"
    if "jest" in pkg:
        return "jest"
    # Check Python
    for f in ("requirements.txt", "pyproject.toml"):
        try:
            content = (cruc / f).read_text(encoding="utf-8")
            if "pytest" in content:
                return "pytest"
        except OSError:
            pass
    return "playwright"  # default


def _default_docker(args: list[str], **kw: Any) -> Any:
    from dark_factory.integrations.shell import docker  # noqa: PLC0415
    return docker(args, **kw)
