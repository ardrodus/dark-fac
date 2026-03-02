"""Wire pipeline events to the TUI dashboard panels.

Maps each of the pipeline event types from :mod:`factory.engine.events`
to the appropriate dashboard widget updates (PipelinePanel, AgentPanel,
GatePanel, LogPanel, NotificationPanel).

Usage::

    from factory.ui.event_wiring import wire_events

    app = DashboardApp(state=state)
    emitter = EventEmitter(on_event=wire_events(app, state))

The returned callback is suitable for passing directly to
:class:`~factory.engine.events.EventEmitter` or the ``on_event``
parameter of :func:`~factory.engine.sdk.execute`.
"""

from __future__ import annotations

from collections.abc import Callable

from factory.engine.events import (
    CheckpointSaved,
    InterviewCompleted,
    InterviewStarted,
    InterviewTimeout,
    ParallelBranchCompleted,
    ParallelBranchStarted,
    ParallelCompleted,
    ParallelStarted,
    PipelineCompleted,
    PipelineEvent,
    PipelineFailed,
    PipelineStarted,
    StageCompleted,
    StageFailed,
    StageRetrying,
    StageStarted,
)
from factory.ui.dashboard import (
    AgentInfo,
    DashboardApp,
    DashboardState,
    GateSummary,
    StageStatus,
)
from factory.ui.notifications import notify


def _find_stage(state: DashboardState, name: str) -> int | None:
    """Return the index of a stage by name, or None."""
    for i, s in enumerate(state.stages):
        return i if s.name == name else None
    return None


def _upsert_stage(state: DashboardState, name: str, new: StageStatus) -> None:
    """Replace the stage matching *name*, or append if not found."""
    for i, s in enumerate(state.stages):
        if s.name == name:
            state.stages[i] = new
            return
    state.stages.append(new)


def _find_agent(state: DashboardState, role: str) -> int | None:
    """Return the index of an agent by role, or None."""
    for i, a in enumerate(state.agents):
        if a.role == role:
            return i
    return None


def _upsert_agent(state: DashboardState, role: str, new: AgentInfo) -> None:
    """Replace the agent matching *role*, or append if not found."""
    for i, a in enumerate(state.agents):
        if a.role == role:
            state.agents[i] = new
            return
    state.agents.append(new)


# ── Per-event handlers ────────────────────────────────────────────


def _on_pipeline_started(
    app: DashboardApp, state: DashboardState, event: PipelineStarted,
) -> None:
    app.log_message(f"[bold]Pipeline '{event.name}' started[/bold] (id={event.id})")
    notify("Pipeline started", f"'{event.name}' (id={event.id})", level="info")


def _on_pipeline_completed(
    app: DashboardApp, state: DashboardState, event: PipelineCompleted,
) -> None:
    app.log_message(
        f"[bold green]Pipeline completed[/bold green] in {event.duration:.1f}s"
        f" ({event.artifact_count} artifacts)"
    )
    notify(
        "Pipeline completed",
        f"{event.duration:.1f}s, {event.artifact_count} artifacts",
        level="success",
    )


def _on_pipeline_failed(
    app: DashboardApp, state: DashboardState, event: PipelineFailed,
) -> None:
    app.log_message(
        f"[bold red]Pipeline failed[/bold red] after {event.duration:.1f}s: {event.error}"
    )
    notify("Pipeline failed", event.error, level="error")


def _on_stage_started(
    app: DashboardApp, state: DashboardState, event: StageStarted,
) -> None:
    _upsert_stage(
        state, event.name,
        StageStatus(name=event.name, state="running", detail=f"index={event.index}"),
    )
    app.log_message(f"Stage [bold]'{event.name}'[/bold] started")


def _on_stage_completed(
    app: DashboardApp, state: DashboardState, event: StageCompleted,
) -> None:
    _upsert_stage(
        state, event.name,
        StageStatus(
            name=event.name, state="passed",
            detail=f"index={event.index}",
            duration_ms=event.duration * 1000,
        ),
    )
    app.log_message(f"Stage '{event.name}' [green]completed[/green] in {event.duration:.1f}s")


def _on_stage_failed(
    app: DashboardApp, state: DashboardState, event: StageFailed,
) -> None:
    _upsert_stage(
        state, event.name,
        StageStatus(
            name=event.name, state="failed",
            detail=event.error[:60],
        ),
    )
    app.log_message(f"Stage '{event.name}' [red]FAILED[/red]: {event.error}")


def _on_stage_retrying(
    app: DashboardApp, state: DashboardState, event: StageRetrying,
) -> None:
    _upsert_stage(
        state, event.name,
        StageStatus(
            name=event.name, state="running",
            detail=f"retry #{event.attempt}",
        ),
    )
    app.log_message(
        f"Stage '{event.name}' retrying (attempt {event.attempt}, delay {event.delay:.1f}s)"
    )


def _on_parallel_started(
    app: DashboardApp, state: DashboardState, event: ParallelStarted,
) -> None:
    app.log_message(f"Parallel execution started ({event.branch_count} branches)")


def _on_parallel_branch_started(
    app: DashboardApp, state: DashboardState, event: ParallelBranchStarted,
) -> None:
    _upsert_agent(
        state, event.branch,
        AgentInfo(role=event.branch, status="active", task=f"branch [{event.index}]"),
    )
    app.log_message(f"  Branch '{event.branch}' [{event.index}] started")


def _on_parallel_branch_completed(
    app: DashboardApp, state: DashboardState, event: ParallelBranchCompleted,
) -> None:
    status = "idle" if event.success else "error"
    _upsert_agent(
        state, event.branch,
        AgentInfo(
            role=event.branch, status=status,
            task=f"{'done' if event.success else 'failed'} in {event.duration:.1f}s",
        ),
    )
    outcome = "succeeded" if event.success else "failed"
    app.log_message(f"  Branch '{event.branch}' [{event.index}] {outcome} in {event.duration:.1f}s")


def _on_parallel_completed(
    app: DashboardApp, state: DashboardState, event: ParallelCompleted,
) -> None:
    app.log_message(
        f"Parallel execution completed in {event.duration:.1f}s"
        f" ({event.success_count} ok, {event.failure_count} failed)"
    )


def _on_interview_started(
    app: DashboardApp, state: DashboardState, event: InterviewStarted,
) -> None:
    state.gate_summaries.append(
        GateSummary(
            name=event.stage, passed=False, check_count=0,
            detail=f"Awaiting: {event.question[:40]}",
        ),
    )
    app.log_message(f"Gate interview in '{event.stage}': {event.question}")


def _on_interview_completed(
    app: DashboardApp, state: DashboardState, event: InterviewCompleted,
) -> None:
    # Update existing gate entry to PASS
    for i, gs in enumerate(state.gate_summaries):
        if gs.detail.startswith("Awaiting:"):
            state.gate_summaries[i] = GateSummary(
                name=gs.name, passed=True, check_count=1,
                detail=f"Answered in {event.duration:.1f}s",
            )
            break
    app.log_message(f"Gate interview completed in {event.duration:.1f}s")


def _on_interview_timeout(
    app: DashboardApp, state: DashboardState, event: InterviewTimeout,
) -> None:
    # Update existing gate entry to BLOCK
    for i, gs in enumerate(state.gate_summaries):
        if gs.name == event.stage and not gs.passed:
            state.gate_summaries[i] = GateSummary(
                name=gs.name, passed=False, check_count=1,
                detail=f"Timed out after {event.duration:.1f}s",
            )
            break
    app.log_message(f"Gate interview timed out in '{event.stage}' after {event.duration:.1f}s")


def _on_checkpoint_saved(
    app: DashboardApp, state: DashboardState, event: CheckpointSaved,
) -> None:
    app.log_message(f"Checkpoint saved at '{event.node_id}'")


# ── Dispatch table ────────────────────────────────────────────────

_DISPATCH: dict[
    type[PipelineEvent],
    Callable[[DashboardApp, DashboardState, PipelineEvent], None],
] = {
    PipelineStarted: _on_pipeline_started,  # type: ignore[dict-item]
    PipelineCompleted: _on_pipeline_completed,  # type: ignore[dict-item]
    PipelineFailed: _on_pipeline_failed,  # type: ignore[dict-item]
    StageStarted: _on_stage_started,  # type: ignore[dict-item]
    StageCompleted: _on_stage_completed,  # type: ignore[dict-item]
    StageFailed: _on_stage_failed,  # type: ignore[dict-item]
    StageRetrying: _on_stage_retrying,  # type: ignore[dict-item]
    ParallelStarted: _on_parallel_started,  # type: ignore[dict-item]
    ParallelBranchStarted: _on_parallel_branch_started,  # type: ignore[dict-item]
    ParallelBranchCompleted: _on_parallel_branch_completed,  # type: ignore[dict-item]
    ParallelCompleted: _on_parallel_completed,  # type: ignore[dict-item]
    InterviewStarted: _on_interview_started,  # type: ignore[dict-item]
    InterviewCompleted: _on_interview_completed,  # type: ignore[dict-item]
    InterviewTimeout: _on_interview_timeout,  # type: ignore[dict-item]
    CheckpointSaved: _on_checkpoint_saved,  # type: ignore[dict-item]
}


def wire_events(
    app: DashboardApp,
    state: DashboardState,
) -> Callable[[PipelineEvent], None]:
    """Return an ``on_event`` callback that routes events to dashboard panels.

    The returned callable is compatible with
    :class:`~factory.engine.events.EventEmitter` and the ``on_event``
    parameter of :func:`~factory.engine.sdk.execute`.

    Args:
        app: The running Textual dashboard application.
        state: The mutable dashboard state shared with the app.

    Returns:
        A callback ``(PipelineEvent) -> None``.
    """

    def _handler(event: PipelineEvent) -> None:
        handler_fn = _DISPATCH.get(type(event))
        if handler_fn is not None:
            handler_fn(app, state, event)

    return _handler
