"""Crucible coordinator — two-round validation with test graduation.

Sequences the full Crucible pipeline:
1. Provision crucible repo + PR branch
2. Detect frameworks, install missing
3. Containerize app + spin up digital twins
4. Generate scenario tests from PR diff
5. ROUND 1: Run new tests only (smoke)
6. ROUND 2: Run all tests (regression)
7. Graduate tests (create PR to crucible repo)
8. Teardown containers

The "bill becoming a law" — new tests are validated in two rounds, then
graduated into the permanent crucible test suite via a PR.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dark_factory.crucible.orchestrator import (
    CrucibleConfig,
    CrucibleResult,
    CrucibleVerdict,
    PhaseMetrics,
    TestResult,
    _build,
    _capture,
    _cf,
    _down,
    _health,
    _save,
    _timed,
    _up,
)

if TYPE_CHECKING:
    from dark_factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CrucibleCoordinatorConfig:
    """Full configuration for a two-round Crucible run."""

    # Docker lifecycle
    build_timeout: int = 300
    health_timeout: int = 60
    test_timeout: int = 600
    compose_file: str = ""
    project_name: str = "crucible"
    num_shards: int = 1

    # Two-round specific
    crucible_repo: str = ""  # owner/repo-crucible
    pr_number: int = 0
    pr_branch: str = ""
    pr_diff: str = ""
    pr_title: str = ""
    app_repo: str = ""  # owner/repo
    auto_graduate: bool = True  # create PR on success
    smoke_timeout: int = 300  # round 1 timeout
    regression_timeout: int = 600  # round 2 timeout

    # Callables (for testing)
    docker_fn: Any = None
    agent_fn: Any = None


@dataclass(frozen=True, slots=True)
class TwoRoundResult:
    """Full outcome of the two-round Crucible pipeline."""

    verdict: CrucibleVerdict
    round1_result: Any = None  # RunResult | None
    round2_result: Any = None  # RunResult | None
    graduation_result: Any = None  # GraduationResult | None
    framework_detection: Any = None  # DetectionResult | None
    scenario_generation: Any = None  # ScenarioGenResult | None
    phases: tuple[PhaseMetrics, ...] = ()
    duration_s: float = 0.0
    error: str = ""


def _to_crucible_config(cfg: CrucibleCoordinatorConfig, *, timeout: int = 0) -> CrucibleConfig:
    """Convert coordinator config to orchestrator config."""
    return CrucibleConfig(
        build_timeout=cfg.build_timeout,
        health_timeout=cfg.health_timeout,
        test_timeout=timeout or cfg.test_timeout,
        compose_file=cfg.compose_file,
        project_name=cfg.project_name,
        docker_fn=cfg.docker_fn,
        repo=cfg.crucible_repo,
        num_shards=cfg.num_shards,
    )


# ── Phase Implementations ──────────────────────────────────────


def _phase_provision_crucible(
    workspace: Workspace, cfg: CrucibleCoordinatorConfig,
) -> tuple[PhaseMetrics, str]:
    """Clone or pull the crucible test repo."""
    from dark_factory.crucible.repo_provision import manage_crucible_repo  # noqa: PLC0415

    t0 = time.monotonic()
    crucible_local = Path(workspace.path) / ".dark-factory" / "crucible-tests"
    try:
        if cfg.crucible_repo:
            result = manage_crucible_repo(
                cfg.crucible_repo,
                target_dir=crucible_local,
            )
            ok = not result.error
            detail = result.error or f"synced {result.crucible_repo}"
            local = result.local_path or str(crucible_local)
        else:
            # No crucible repo configured — scaffold locally
            crucible_local.mkdir(parents=True, exist_ok=True)
            ok = True
            detail = "local scaffold (no remote repo)"
            local = str(crucible_local)
    except Exception as exc:  # noqa: BLE001
        ok, detail, local = False, str(exc), ""
    pm = PhaseMetrics(
        phase="provision-crucible",
        duration_s=time.monotonic() - t0,
        passed=ok, detail=detail,
    )
    return pm, local


def _phase_detect_frameworks(
    workspace: Workspace, crucible_path: str, cfg: CrucibleCoordinatorConfig,
) -> tuple[PhaseMetrics, Any]:
    """Detect which test frameworks are needed."""
    from dark_factory.crucible.framework_detect import (  # noqa: PLC0415
        DetectionResult,
        detect_frameworks,
        ensure_frameworks,
    )

    t0 = time.monotonic()
    try:
        result = detect_frameworks(workspace.path, crucible_path)
        # Install missing frameworks
        if result.missing_frameworks:
            ensure_frameworks(crucible_path, result)
        ok = True
        detail = (
            f"detected: {', '.join(f.name for f in result.recommended_frameworks)} "
            f"missing: {', '.join(result.missing_frameworks)}"
        )
    except Exception as exc:  # noqa: BLE001
        result = None
        ok, detail = False, str(exc)
    pm = PhaseMetrics(
        phase="detect-frameworks",
        duration_s=time.monotonic() - t0,
        passed=ok, detail=detail,
    )
    return pm, result


def _phase_containerize(
    workspace: Workspace, cfg: CrucibleCoordinatorConfig,
) -> PhaseMetrics:
    """Build and start the containerized app + twins."""
    oc = _to_crucible_config(cfg)
    t0 = time.monotonic()
    # Build
    if not _build(workspace, oc):
        return PhaseMetrics(
            phase="containerize", duration_s=time.monotonic() - t0,
            passed=False, detail="Docker build failed",
        )
    # Up
    if not _up(workspace, oc):
        return PhaseMetrics(
            phase="containerize", duration_s=time.monotonic() - t0,
            passed=False, detail="Docker compose up failed",
        )
    # Health
    if not _health(oc):
        return PhaseMetrics(
            phase="containerize", duration_s=time.monotonic() - t0,
            passed=False, detail="Health check failed",
        )
    return PhaseMetrics(
        phase="containerize", duration_s=time.monotonic() - t0,
        passed=True, detail="app + twins healthy",
    )


def _phase_generate_scenarios(
    workspace: Workspace,
    crucible_path: str,
    cfg: CrucibleCoordinatorConfig,
    detection: Any,
) -> tuple[PhaseMetrics, Any]:
    """Generate scenario tests from PR diff."""
    from dark_factory.crucible.scenario_gen import (  # noqa: PLC0415
        generate_scenarios,
        write_scenarios,
    )

    t0 = time.monotonic()
    try:
        frameworks = detection.recommended_frameworks if detection else ()
        result = generate_scenarios(
            workspace.path, crucible_path,
            cfg.pr_number, cfg.pr_diff, frameworks,
            pr_title=cfg.pr_title,
            agent_fn=cfg.agent_fn,
        )
        if result.error:
            return (
                PhaseMetrics(
                    phase="generate-scenarios",
                    duration_s=time.monotonic() - t0,
                    passed=False, detail=result.error,
                ),
                result,
            )
        # Write test files
        written = write_scenarios(crucible_path, result)
        detail = f"{len(written)} test files written"
    except Exception as exc:  # noqa: BLE001
        result = None
        detail = str(exc)
        return (
            PhaseMetrics(
                phase="generate-scenarios",
                duration_s=time.monotonic() - t0,
                passed=False, detail=detail,
            ),
            result,
        )
    return (
        PhaseMetrics(
            phase="generate-scenarios",
            duration_s=time.monotonic() - t0,
            passed=True, detail=detail,
        ),
        result,
    )


def _phase_round1_smoke(
    workspace: Workspace,
    crucible_path: str,
    cfg: CrucibleCoordinatorConfig,
    scenario_result: Any,
    detection: Any,
) -> tuple[PhaseMetrics, Any]:
    """ROUND 1: Run only newly generated PR tests (smoke)."""
    from dark_factory.crucible.test_runner import RunResult, TestMode, run_tests  # noqa: PLC0415

    t0 = time.monotonic()
    oc = _to_crucible_config(cfg, timeout=cfg.smoke_timeout)
    test_files = [t.file_path for t in scenario_result.tests] if scenario_result else []
    fw = ""
    if detection and detection.recommended_frameworks:
        fw = detection.recommended_frameworks[0].name

    try:
        result = run_tests(
            workspace, crucible_path, oc,
            mode=TestMode.SMOKE,
            pr_number=cfg.pr_number,
            test_files=test_files or None,
            framework=fw,
            docker_fn=cfg.docker_fn,
        )
    except Exception as exc:  # noqa: BLE001
        result = RunResult(
            mode=TestMode.SMOKE, verdict=CrucibleVerdict.NO_GO.value,
            pass_count=0, fail_count=0, skip_count=0,
            duration_s=time.monotonic() - t0, error=str(exc),
        )
    pm = PhaseMetrics(
        phase="round1-smoke",
        duration_s=time.monotonic() - t0,
        passed=result.verdict == CrucibleVerdict.GO.value,
        detail=f"pass={result.pass_count} fail={result.fail_count} skip={result.skip_count}",
    )
    return pm, result


def _phase_round2_regression(
    workspace: Workspace,
    crucible_path: str,
    cfg: CrucibleCoordinatorConfig,
    detection: Any,
) -> tuple[PhaseMetrics, Any]:
    """ROUND 2: Run ALL existing crucible tests + new tests."""
    from dark_factory.crucible.test_runner import RunResult, TestMode, run_tests  # noqa: PLC0415

    t0 = time.monotonic()
    oc = _to_crucible_config(cfg, timeout=cfg.regression_timeout)
    fw = ""
    if detection and detection.recommended_frameworks:
        fw = detection.recommended_frameworks[0].name

    try:
        result = run_tests(
            workspace, crucible_path, oc,
            mode=TestMode.FULL,
            pr_number=cfg.pr_number,
            framework=fw,
            docker_fn=cfg.docker_fn,
        )
    except Exception as exc:  # noqa: BLE001
        result = RunResult(
            mode=TestMode.FULL, verdict=CrucibleVerdict.NO_GO.value,
            pass_count=0, fail_count=0, skip_count=0,
            duration_s=time.monotonic() - t0, error=str(exc),
        )
    pm = PhaseMetrics(
        phase="round2-regression",
        duration_s=time.monotonic() - t0,
        passed=result.verdict == CrucibleVerdict.GO.value,
        detail=f"pass={result.pass_count} fail={result.fail_count} skip={result.skip_count}",
    )
    return pm, result


def _phase_graduate(
    crucible_path: str,
    cfg: CrucibleCoordinatorConfig,
    scenario_result: Any,
) -> tuple[PhaseMetrics, Any]:
    """Graduate tests — create PR to crucible repo."""
    from dark_factory.crucible.graduation import graduate_tests  # noqa: PLC0415

    t0 = time.monotonic()
    try:
        result = graduate_tests(
            crucible_path, scenario_result,
            cfg.app_repo, cfg.pr_number,
        )
        ok = result.graduated
        detail = result.pr_url if ok else (result.error or "graduation skipped")
    except Exception as exc:  # noqa: BLE001
        result = None
        ok, detail = False, str(exc)
    pm = PhaseMetrics(
        phase="graduate-tests",
        duration_s=time.monotonic() - t0,
        passed=ok, detail=detail,
    )
    return pm, result


def _phase_teardown(workspace: Workspace, cfg: CrucibleCoordinatorConfig) -> PhaseMetrics:
    """Tear down containers."""
    oc = _to_crucible_config(cfg)
    return _timed("teardown", lambda: _down(oc, workspace), 30)


# ── Public API ──────────────────────────────────────────────────


def run_crucible_pipeline(
    workspace: Workspace,
    config: CrucibleCoordinatorConfig,
) -> TwoRoundResult:
    """Run the full two-round Crucible pipeline.

    Sequence:
    1. Provision crucible repo (clone/pull)
    2. Detect frameworks, install missing
    3. Containerize app + spin up twins
    4. Generate scenario tests from PR diff
    5. ROUND 1: Run new tests only (smoke)
    6. ROUND 2: Run all tests (regression)
    7. Graduate tests (create PR to crucible repo)
    8. Teardown containers

    Always tears down containers, even on failure.
    """
    t0 = time.monotonic()
    phases: list[PhaseMetrics] = []
    crucible_path = ""
    detection = None
    scenario_result = None
    round1 = None
    round2 = None
    graduation = None

    def _fail(error: str, verdict: CrucibleVerdict = CrucibleVerdict.NO_GO) -> TwoRoundResult:
        # Always teardown
        phases.append(_phase_teardown(workspace, config))
        return TwoRoundResult(
            verdict=verdict,
            round1_result=round1,
            round2_result=round2,
            graduation_result=graduation,
            framework_detection=detection,
            scenario_generation=scenario_result,
            phases=tuple(phases),
            duration_s=time.monotonic() - t0,
            error=error,
        )

    # 1. Provision crucible repo
    pm, crucible_path = _phase_provision_crucible(workspace, config)
    phases.append(pm)
    if not pm.passed:
        logger.warning("Crucible provision failed: %s (continuing with local path)", pm.detail)
        crucible_path = str(Path(workspace.path) / ".dark-factory" / "crucible-tests")

    # 2. Detect frameworks
    pm, detection = _phase_detect_frameworks(workspace, crucible_path, config)
    phases.append(pm)
    if not pm.passed:
        logger.warning("Framework detection failed: %s (using defaults)", pm.detail)

    # 3. Containerize app + twins
    pm = _phase_containerize(workspace, config)
    phases.append(pm)
    if not pm.passed:
        return _fail(f"Containerize failed: {pm.detail}")

    # 4. Generate scenario tests
    pm, scenario_result = _phase_generate_scenarios(
        workspace, crucible_path, config, detection,
    )
    phases.append(pm)
    if not pm.passed:
        return _fail(f"Scenario generation failed: {pm.detail}")

    # 5. ROUND 1: Smoke (new PR tests only)
    pm, round1 = _phase_round1_smoke(
        workspace, crucible_path, config, scenario_result, detection,
    )
    phases.append(pm)
    if not pm.passed:
        logger.error("Round 1 (smoke) failed — new tests did not pass")
        return _fail("Round 1 (smoke) failed: new PR tests did not pass")

    # 6. ROUND 2: Full regression (all tests)
    pm, round2 = _phase_round2_regression(
        workspace, crucible_path, config, detection,
    )
    phases.append(pm)
    if not pm.passed:
        verdict_str = round2.verdict if round2 else CrucibleVerdict.NO_GO.value
        if verdict_str == CrucibleVerdict.NEEDS_LIVE.value:
            return _fail("Round 2 needs live services", CrucibleVerdict.NEEDS_LIVE)
        return _fail("Round 2 (regression) failed")

    # 7. Graduate tests (bill becomes law)
    if config.auto_graduate and scenario_result and scenario_result.tests:
        pm, graduation = _phase_graduate(crucible_path, config, scenario_result)
        phases.append(pm)
        if not pm.passed:
            logger.warning("Graduation failed: %s (tests passed but not graduated)", pm.detail)

    # 8. Teardown
    phases.append(_phase_teardown(workspace, config))

    logger.info(
        "Crucible pipeline: GO (round1=%s round2=%s graduated=%s %.1fs)",
        round1.verdict if round1 else "?",
        round2.verdict if round2 else "?",
        bool(graduation and graduation.graduated) if graduation else False,
        time.monotonic() - t0,
    )

    return TwoRoundResult(
        verdict=CrucibleVerdict.GO,
        round1_result=round1,
        round2_result=round2,
        graduation_result=graduation,
        framework_detection=detection,
        scenario_generation=scenario_result,
        phases=tuple(phases),
        duration_s=time.monotonic() - t0,
    )


def to_crucible_result(two_round: TwoRoundResult) -> CrucibleResult:
    """Map TwoRoundResult back to CrucibleResult for backward compatibility."""
    # Merge test results from both rounds
    all_tests: list[TestResult] = []
    pc = fc = sc = 0
    screenshots: list[str] = []
    logs = ""

    for rnd in (two_round.round1_result, two_round.round2_result):
        if rnd is None:
            continue
        all_tests.extend(rnd.test_results)
        pc += rnd.pass_count
        fc += rnd.fail_count
        sc += rnd.skip_count
        if rnd.logs:
            logs += f"\n--- {rnd.mode.value} ---\n{rnd.logs}"
        screenshots.extend(rnd.screenshots)

    return CrucibleResult(
        verdict=two_round.verdict,
        test_results=tuple(all_tests),
        screenshots=tuple(screenshots),
        logs=logs,
        phases=two_round.phases,
        pass_count=pc,
        fail_count=fc,
        skip_count=sc,
        duration_s=two_round.duration_s,
        error=two_round.error,
    )
