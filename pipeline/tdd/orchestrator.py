"""TDD pipeline orchestrator — sequence Test Writer, Feature Writer, Code Reviewer.

Feedback loops on review REQUEST_CHANGES or test failure (max 3 rounds).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.workspace.manager import Workspace

from factory.pipeline.tdd.code_reviewer import (
    CodeReviewResult,
    ReviewVerdict,
    run_code_reviewer,
)
from factory.pipeline.tdd.feature_writer import (
    FeatureWriterResult,
    TestRunResult,
    run_feature_writer,
)
from factory.pipeline.tdd.test_writer import SpecBundle, TestWriterResult, run_test_writer

_T = TypeVar("_T")
logger = logging.getLogger(__name__)
_MAX_ROUNDS = 3


@dataclass(frozen=True, slots=True)
class TDDConfig:
    """Settings for a TDD pipeline run."""
    max_rounds: int = _MAX_ROUNDS
    test_command: tuple[str, ...] = ("pytest", "-v", "--tb=short")
    test_timeout: int = 120


@dataclass(frozen=True, slots=True)
class StageMetrics:
    """Duration of a single pipeline stage."""
    stage: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class TDDResult:
    """Outcome of a full TDD pipeline run."""
    success: bool
    rounds: int
    test_results: tuple[TestRunResult, ...]
    review_result: CodeReviewResult | None = None
    test_writer_result: TestWriterResult | None = None
    feature_writer_results: tuple[FeatureWriterResult, ...] = ()
    files_changed: tuple[str, ...] = ()
    stage_metrics: tuple[StageMetrics, ...] = ()
    errors: tuple[str, ...] = field(default_factory=tuple)


def _run_tests(
    workspace_path: str, config: TDDConfig, *,
    run_fn: Callable[[str, TDDConfig], TestRunResult] | None = None,
) -> TestRunResult:
    """Execute the test suite in *workspace_path*."""
    if run_fn is not None:
        return run_fn(workspace_path, config)
    from factory.integrations.shell import run_command  # noqa: PLC0415

    result = run_command(
        list(config.test_command), timeout=config.test_timeout,
        check=False, cwd=workspace_path,
    )
    passed = result.returncode == 0
    output = result.stdout + result.stderr
    return TestRunResult(
        passed=passed, raw_output=output,
        total=output.count("PASSED") + output.count("FAILED"),
        failures=output.count("FAILED"),
    )


def _get_diff(workspace_path: str) -> str:
    """Return the current git diff for the workspace."""
    from factory.integrations.shell import git  # noqa: PLC0415

    result = git(["diff", "HEAD~1"], cwd=workspace_path, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        result = git(["diff"], cwd=workspace_path, check=False)
    return result.stdout


def _timed(fn: Callable[[], _T], name: str) -> tuple[_T, StageMetrics]:
    """Run *fn*, returning (result, metrics)."""
    start = time.monotonic()
    result = fn()
    elapsed = time.monotonic() - start
    logger.info("Stage %s completed in %.1fs", name, elapsed)
    return result, StageMetrics(stage=name, duration_seconds=round(elapsed, 2))


def _collect_files(*fw_results: FeatureWriterResult) -> tuple[str, ...]:
    """Gather unique file paths from all Feature Writer results."""
    seen: dict[str, None] = {}
    for fw in fw_results:
        for f in (*fw.files_modified, *fw.files_created):
            seen.setdefault(f, None)
    return tuple(seen)


def _make_result(  # noqa: PLR0913
    success: bool, rounds: int, test_results: list[TestRunResult],
    review: CodeReviewResult | None, tw: TestWriterResult,
    fw_results: list[FeatureWriterResult],
    metrics: list[StageMetrics], errors: list[str],
) -> TDDResult:
    return TDDResult(
        success=success, rounds=rounds, test_results=tuple(test_results),
        review_result=review, test_writer_result=tw,
        feature_writer_results=tuple(fw_results),
        files_changed=_collect_files(*fw_results),
        stage_metrics=tuple(metrics), errors=tuple(errors),
    )


def run_tdd_pipeline(
    specs: SpecBundle, workspace: Workspace, config: TDDConfig | None = None,
    *, invoke_fn: Callable[[str], str] | None = None,
    test_run_fn: Callable[[str, TDDConfig], TestRunResult] | None = None,
) -> TDDResult:
    """Orchestrate the full TDD cycle with feedback loops."""
    if config is None:
        config = TDDConfig()
    ws_path = workspace.path
    metrics: list[StageMetrics] = []
    test_results: list[TestRunResult] = []
    fw_results: list[FeatureWriterResult] = []
    errors: list[str] = []

    # Stage 1: Test Writer
    logger.info("TDD pipeline: starting Test Writer")
    tw_result, tw_m = _timed(
        lambda: run_test_writer(specs, workspace, invoke_fn=invoke_fn),
        "test_writer",
    )

    metrics.append(tw_m)
    if tw_result.errors:
        errors.extend(tw_result.errors)
    if not tw_result.test_files_created:
        logger.error("Test Writer produced no test files — aborting pipeline")
        return TDDResult(
            success=False, rounds=0, test_results=(),
            test_writer_result=tw_result, errors=tuple(errors),
            stage_metrics=tuple(metrics),
        )

    # Stage 2: Run tests — expect failure (red phase)
    logger.info("TDD pipeline: running tests (expect failure)")
    red_result, red_m = _timed(
        lambda: _run_tests(ws_path, config, run_fn=test_run_fn), "test_red",
    )

    metrics.append(red_m)
    test_results.append(red_result)
    if red_result.passed:
        logger.warning("Tests passed before implementation — continuing anyway")

    # Feedback loop: Feature Writer + tests + Code Reviewer
    cur_test = red_result
    review_result: CodeReviewResult | None = None
    review_feedback = ""

    for rnd in range(1, config.max_rounds + 1):
        logger.info("TDD pipeline: round %d/%d", rnd, config.max_rounds)

        # Stage 3: Feature Writer
        fw_input = cur_test
        if review_feedback:
            fw_input = TestRunResult(
                passed=cur_test.passed, total=cur_test.total,
                failures=cur_test.failures, test_names=cur_test.test_names,
                failure_messages=(*cur_test.failure_messages, review_feedback),
                raw_output=cur_test.raw_output,
            )
        fw_r, fw_m = _timed(
            lambda: run_feature_writer(specs, workspace, fw_input, invoke_fn=invoke_fn),
            f"feature_writer_r{rnd}",
        )
    
        metrics.append(fw_m)
        fw_results.append(fw_r)
        if fw_r.errors:
            errors.extend(fw_r.errors)

        # Stage 4: Run tests — expect pass (green phase)
        green_r, green_m = _timed(
            lambda: _run_tests(ws_path, config, run_fn=test_run_fn),
            f"test_green_r{rnd}",
        )
    
        metrics.append(green_m)
        test_results.append(green_r)

        if not green_r.passed:
            logger.warning("Tests still failing (round %d)", rnd)
            cur_test, review_feedback = green_r, ""
            if rnd >= config.max_rounds:
                errors.append(f"Tests still failing after {config.max_rounds} rounds")
            continue

        # Stage 5: Code Reviewer
        diff = _get_diff(ws_path)
        cr_r, cr_m = _timed(
            lambda: run_code_reviewer(
                specs, workspace, diff,
                test_results=green_r.raw_output, invoke_fn=invoke_fn,
            ),
            f"code_review_r{rnd}",
        )
    
        metrics.append(cr_m)
        review_result = cr_r

        if cr_r.verdict == ReviewVerdict.APPROVE:
            logger.info("TDD pipeline PASSED at round %d", rnd)
            return _make_result(
                True, rnd, test_results, review_result, tw_result,
                fw_results, metrics, errors,
            )

        logger.info("Code review: REQUEST_CHANGES (round %d)", rnd)
        review_feedback = cr_r.raw_output
        cur_test = green_r
        if rnd >= config.max_rounds:
            errors.append(
                f"Code review requesting changes after {config.max_rounds} rounds"
            )

    logger.warning("TDD pipeline FAILED after %d rounds", config.max_rounds)
    return _make_result(
        False, config.max_rounds, test_results, review_result,
        tw_result, fw_results, metrics, errors,
    )
