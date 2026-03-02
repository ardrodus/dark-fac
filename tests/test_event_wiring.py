"""Tests for factory.ui.event_wiring — pipeline event → dashboard panel routing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dark_factory.engine.events import (
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
from dark_factory.ui.dashboard import DashboardState
from dark_factory.ui.event_wiring import wire_events


@pytest.fixture()
def state() -> DashboardState:
    return DashboardState()


@pytest.fixture()
def app() -> MagicMock:
    return MagicMock(spec=["log_message"])


@pytest.fixture()
def handler(app: MagicMock, state: DashboardState):  # noqa: ANN201
    return wire_events(app, state)


# ── Pipeline lifecycle ───────────────────────────────────────────


def test_pipeline_started_logs(handler, app: MagicMock, state: DashboardState) -> None:
    handler(PipelineStarted(name="test-pipe", id="abc123"))
    app.log_message.assert_called_once()
    assert "test-pipe" in app.log_message.call_args[0][0]


def test_pipeline_completed_logs(handler, app: MagicMock) -> None:
    handler(PipelineCompleted(duration=12.5, artifact_count=3))
    app.log_message.assert_called_once()
    msg = app.log_message.call_args[0][0]
    assert "12.5" in msg
    assert "3" in msg


def test_pipeline_failed_logs(handler, app: MagicMock) -> None:
    handler(PipelineFailed(error="kaboom", duration=5.0))
    app.log_message.assert_called_once()
    assert "kaboom" in app.log_message.call_args[0][0]


# ── Stage transitions → PipelinePanel ────────────────────────────


def test_stage_started_sets_running(handler, state: DashboardState) -> None:
    handler(StageStarted(name="build", index=0))
    assert len(state.stages) == 1
    assert state.stages[0].name == "build"
    assert state.stages[0].state == "running"


def test_stage_completed_sets_passed(handler, state: DashboardState) -> None:
    handler(StageStarted(name="build", index=0))
    handler(StageCompleted(name="build", index=0, duration=2.0))
    assert state.stages[0].state == "passed"
    assert state.stages[0].duration_ms == pytest.approx(2000.0)


def test_stage_failed_sets_failed(handler, state: DashboardState) -> None:
    handler(StageStarted(name="test", index=1))
    handler(StageFailed(name="test", index=1, error="assertion error", will_retry=False))
    assert state.stages[0].state == "failed"
    assert "assertion" in state.stages[0].detail


def test_stage_retrying_keeps_running(handler, state: DashboardState) -> None:
    handler(StageStarted(name="deploy", index=2))
    handler(StageRetrying(name="deploy", index=2, attempt=1, delay=1.5, error="timeout"))
    assert state.stages[0].state == "running"
    assert "retry" in state.stages[0].detail


# ── Agent activity → AgentPanel ──────────────────────────────────


def test_parallel_branch_started_adds_active_agent(handler, state: DashboardState) -> None:
    handler(ParallelBranchStarted(branch="codegen", index=0))
    assert len(state.agents) == 1
    assert state.agents[0].role == "codegen"
    assert state.agents[0].status == "active"


def test_parallel_branch_completed_success_idle(handler, state: DashboardState) -> None:
    handler(ParallelBranchStarted(branch="lint", index=0))
    handler(ParallelBranchCompleted(branch="lint", index=0, duration=3.0, success=True))
    assert state.agents[0].status == "idle"


def test_parallel_branch_completed_failure_error(handler, state: DashboardState) -> None:
    handler(ParallelBranchStarted(branch="lint", index=0))
    handler(ParallelBranchCompleted(branch="lint", index=0, duration=1.0, success=False))
    assert state.agents[0].status == "error"


# ── Gate verdicts → GatePanel ────────────────────────────────────


def test_interview_started_adds_gate_pending(handler, state: DashboardState) -> None:
    handler(InterviewStarted(question="Approve deploy?", stage="human-gate"))
    assert len(state.gate_summaries) == 1
    assert state.gate_summaries[0].name == "human-gate"
    assert state.gate_summaries[0].passed is False


def test_interview_completed_marks_pass(handler, state: DashboardState) -> None:
    handler(InterviewStarted(question="Approve?", stage="gate1"))
    handler(InterviewCompleted(question="Approve?", answer="yes", duration=5.0))
    assert state.gate_summaries[0].passed is True


def test_interview_timeout_marks_block(handler, state: DashboardState) -> None:
    handler(InterviewStarted(question="Approve?", stage="gate1"))
    handler(InterviewTimeout(question="Approve?", stage="gate1", duration=30.0))
    assert state.gate_summaries[0].passed is False
    assert "Timed out" in state.gate_summaries[0].detail


# ── Log messages → LogPanel ──────────────────────────────────────


def test_all_events_log_something(app: MagicMock, state: DashboardState) -> None:
    """Every known event type should call log_message at least once."""
    handler = wire_events(app, state)
    events: list[PipelineEvent] = [
        PipelineStarted(name="p", id="1"),
        PipelineCompleted(duration=1.0, artifact_count=0),
        PipelineFailed(error="e", duration=1.0),
        StageStarted(name="s", index=0),
        StageCompleted(name="s", index=0, duration=1.0),
        StageFailed(name="s", index=0, error="e", will_retry=False),
        StageRetrying(name="s", index=0, attempt=1, delay=0.5),
        ParallelStarted(branch_count=2),
        ParallelBranchStarted(branch="b", index=0),
        ParallelBranchCompleted(branch="b", index=0, duration=1.0, success=True),
        ParallelCompleted(duration=2.0, success_count=1, failure_count=0),
        InterviewStarted(question="q", stage="g"),
        InterviewCompleted(question="q", answer="a", duration=1.0),
        InterviewTimeout(question="q", stage="g", duration=10.0),
        CheckpointSaved(node_id="n1"),
    ]
    for ev in events:
        handler(ev)
    assert app.log_message.call_count == len(events)


# ── Unknown events are silently ignored ──────────────────────────


def test_unknown_event_ignored(handler, app: MagicMock) -> None:
    handler(PipelineEvent())  # base class — not in dispatch table
    app.log_message.assert_not_called()


# ── Dispatch table covers all concrete event types ───────────────


def test_dispatch_covers_all_event_types() -> None:
    """The dispatch table should have an entry for every concrete event."""
    from dark_factory.ui.event_wiring import _DISPATCH

    expected = {
        PipelineStarted, PipelineCompleted, PipelineFailed,
        StageStarted, StageCompleted, StageFailed, StageRetrying,
        ParallelStarted, ParallelBranchStarted, ParallelBranchCompleted,
        ParallelCompleted,
        InterviewStarted, InterviewCompleted, InterviewTimeout,
        CheckpointSaved,
    }
    assert set(_DISPATCH.keys()) == expected
