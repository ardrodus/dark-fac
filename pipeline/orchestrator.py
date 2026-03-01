"""Pipeline orchestrator — higher-level coordination of pipeline execution.

Provides :func:`run_full_pipeline` and :func:`run_bootstrap_pipeline` as the
primary entry points.  Internally delegates to :mod:`factory.pipeline.runner`
for stage execution, adding:

* **State machine** — tracks IDLE → RUNNING → COMPLETED / FAILED transitions.
* **Recovery delegation** — failure handling routes through the recovery dispatcher.
* **Stage filtering** — bootstrap pipeline runs only plan / implement / test.
* **Attempt history** — records every attempt for diagnostics.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from factory.pipeline.runner import (
    PipelineResult,
    Stage,
    StoryContext,
    run_pipeline,
)
from factory.recovery.dispatcher import ErrorType, handle_failure

logger = logging.getLogger(__name__)


# ── Pipeline state machine ───────────────────────────────────────


class PipelineState(Enum):
    """States the orchestrator can be in."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# Valid transitions: source → set of allowed targets.
_TRANSITIONS: dict[PipelineState, frozenset[PipelineState]] = {
    PipelineState.IDLE: frozenset({PipelineState.RUNNING}),
    PipelineState.RUNNING: frozenset({PipelineState.COMPLETED, PipelineState.FAILED}),
    PipelineState.COMPLETED: frozenset({PipelineState.IDLE}),
    PipelineState.FAILED: frozenset({PipelineState.IDLE, PipelineState.RUNNING}),
}


class InvalidTransitionError(Exception):
    """Raised when a state machine transition is not allowed."""


class PipelineStateMachine:
    """Tracks pipeline execution state with guarded transitions."""

    def __init__(self) -> None:
        self._state = PipelineState.IDLE

    @property
    def state(self) -> PipelineState:
        """Current state."""
        return self._state

    def transition(self, target: PipelineState) -> None:
        """Move to *target* state, or raise :exc:`InvalidTransitionError`."""
        allowed = _TRANSITIONS.get(self._state, frozenset())
        if target not in allowed:
            msg = f"Cannot transition from {self._state.value} to {target.value}"
            raise InvalidTransitionError(msg)
        logger.debug("Pipeline state: %s → %s", self._state.value, target.value)
        self._state = target

    def reset(self) -> None:
        """Force the machine back to IDLE (for retry loops)."""
        if self._state in {PipelineState.COMPLETED, PipelineState.FAILED}:
            self._state = PipelineState.IDLE
        else:
            msg = f"Cannot reset from {self._state.value}"
            raise InvalidTransitionError(msg)


# ── Configuration ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Settings for an orchestrated pipeline run."""

    max_retries: int = 0
    retry_delay_seconds: float = 1.0
    cwd: str | None = None


# ── Bootstrap stage set ──────────────────────────────────────────


BOOTSTRAP_STAGES: tuple[Stage, ...] = (
    Stage.PLAN,
    Stage.IMPLEMENT,
    Stage.TEST,
)


# ── Orchestrator result ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OrchestratorResult:
    """Outcome of an orchestrated pipeline run.

    Attributes
    ----------
    pipeline_result:
        The final (possibly retried) pipeline result.
    state:
        Terminal state of the state machine (COMPLETED or FAILED).
    attempts:
        Total number of execution attempts (1 = no retries).
    history:
        Every :class:`PipelineResult` produced, oldest first.
    """

    pipeline_result: PipelineResult
    state: PipelineState
    attempts: int
    history: tuple[PipelineResult, ...]
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", self.pipeline_result.passed)


# ── Internal helpers ─────────────────────────────────────────────


def _filter_stages(
    result: PipelineResult,
    allowed: tuple[Stage, ...],
) -> PipelineResult:
    """Return a new :class:`PipelineResult` containing only *allowed* stages."""
    filtered = tuple(s for s in result.stages if s.stage in allowed)
    return PipelineResult(story=result.story, stages=filtered)


def _execute_with_retry(
    story: StoryContext,
    config: PipelineConfig,
    allowed_stages: tuple[Stage, ...] | None,
) -> OrchestratorResult:
    """Run the pipeline, delegating failure recovery to the recovery dispatcher.

    Parameters
    ----------
    story:
        The story to process.
    config:
        Retry and execution settings.
    allowed_stages:
        If not *None*, the result is filtered to these stages only.
        The full pipeline still runs (to respect runner internals), but the
        orchestrator evaluates only the allowed subset.
    """
    sm = PipelineStateMachine()
    history: list[PipelineResult] = []

    # ── Initial run ──────────────────────────────────────────────────
    sm.transition(PipelineState.RUNNING)
    logger.info("Pipeline attempt 1 for %r", story.title)

    raw = run_pipeline(story, cwd=config.cwd)
    result = _filter_stages(raw, allowed_stages) if allowed_stages is not None else raw
    history.append(result)

    if result.passed:
        sm.transition(PipelineState.COMPLETED)
        return OrchestratorResult(
            pipeline_result=result,
            state=sm.state,
            attempts=1,
            history=tuple(history),
        )

    # ── Failure — delegate to recovery dispatcher ────────────────────
    sm.transition(PipelineState.FAILED)
    last_result = result

    def _retry_fn(_ctx: str, _details: dict[str, object]) -> bool:
        nonlocal last_result
        sm.reset()
        sm.transition(PipelineState.RUNNING)
        logger.info(
            "Pipeline retry attempt %d for %r",
            len(history) + 1,
            story.title,
        )
        raw_retry = run_pipeline(story, cwd=config.cwd)
        r = _filter_stages(raw_retry, allowed_stages) if allowed_stages is not None else raw_retry
        history.append(r)
        last_result = r
        if r.passed:
            sm.transition(PipelineState.COMPLETED)
            return True
        sm.transition(PipelineState.FAILED)
        return False

    failed_stages = [s for s in result.stages if not s.passed]
    details: dict[str, object] = {
        "story": story.title,
        "failed_stages": [s.stage.value for s in failed_stages],
    }
    sleep_fn = (lambda _: None) if config.retry_delay_seconds == 0.0 else time.sleep

    handle_failure(
        context=f"pipeline:{story.title}",
        error_type=ErrorType.TRANSIENT,
        details=details,
        retry_fn=_retry_fn if config.max_retries > 0 else None,
        max_retries=config.max_retries,
        sleep_fn=sleep_fn,
        cwd=config.cwd,
    )

    return OrchestratorResult(
        pipeline_result=last_result,
        state=sm.state,
        attempts=len(history),
        history=tuple(history),
    )


# ── Public API ───────────────────────────────────────────────────


def run_full_pipeline(
    story: StoryContext,
    config: PipelineConfig | None = None,
) -> OrchestratorResult:
    """Execute the complete 6-stage pipeline.

    Delegates to :func:`factory.pipeline.runner.run_pipeline` and wraps the
    result with state-machine tracking and optional retry.

    Parameters
    ----------
    story:
        The story context to process.
    config:
        Execution settings.  Defaults to no retries.
    """
    if config is None:
        config = PipelineConfig()
    return _execute_with_retry(story, config, allowed_stages=None)


def run_bootstrap_pipeline(
    story: StoryContext,
    config: PipelineConfig | None = None,
) -> OrchestratorResult:
    """Execute the bootstrap pipeline (plan → implement → test only).

    Useful for initial project setup or quick validation cycles where
    quality-gate / review / audit stages are unnecessary.

    Delegates to :func:`factory.pipeline.runner.run_pipeline` and filters
    the result to the three bootstrap stages.

    Parameters
    ----------
    story:
        The story context to process.
    config:
        Execution settings.  Defaults to no retries.
    """
    if config is None:
        config = PipelineConfig()
    return _execute_with_retry(story, config, allowed_stages=BOOTSTRAP_STAGES)
