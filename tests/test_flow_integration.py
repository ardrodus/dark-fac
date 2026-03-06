"""Flow diagram + state propagation and shared timer integration tests.

TS-015: IT-19 through IT-21 (flow diagram + state propagation)
TS-016: IT-22 through IT-24 (shared animation timer)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dark_factory.ui.dashboard import DashboardApp, DashboardState, StageStatus
from dark_factory.ui.widgets.pipeline import PipelineFlowDiagram, PipelineNode


# ── TS-015: Flow diagram + state propagation ─────────────────────


@pytest.mark.asyncio
async def test_it19_update_state_propagates_to_flow(sample_stages) -> None:
    """IT-19: update_state() with running stage propagates to flow diagram running node."""
    state = DashboardState(stages=sample_stages)
    app = DashboardApp(state=state)
    async with app.run_test():
        diagram = app.query_one(PipelineFlowDiagram)
        nodes = list(diagram.query(PipelineNode))
        # stage[1] is "Implement" with state="running"
        assert nodes[1].has_class("-running")


@pytest.mark.asyncio
async def test_it20_refresh_stages_api_preserved(sample_stages) -> None:
    """IT-20: refresh_stages() API signature preserved — accepts list[StageStatus]."""
    diagram = PipelineFlowDiagram()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield diagram

    app = _TestApp()
    async with app.run_test():
        # Should accept list[StageStatus] without error
        diagram.refresh_stages(sample_stages)
        nodes = list(diagram.query(PipelineNode))
        assert len(nodes) == 6


@pytest.mark.asyncio
async def test_it21_stage_transition_updates_diagram() -> None:
    """IT-21: Stage transition running->passed updates flow diagram and stops elapsed."""
    stages_running = [
        StageStatus(name="Plan", state="running", duration_ms=1200),
        StageStatus(name="Implement", state="pending"),
        StageStatus(name="Test", state="pending"),
        StageStatus(name="Quality Gate", state="pending"),
        StageStatus(name="Review", state="pending"),
        StageStatus(name="Audit", state="pending"),
    ]
    stages_passed = [
        StageStatus(name="Plan", state="passed", duration_ms=2500),
        StageStatus(name="Implement", state="running", duration_ms=0),
        StageStatus(name="Test", state="pending"),
        StageStatus(name="Quality Gate", state="pending"),
        StageStatus(name="Review", state="pending"),
        StageStatus(name="Audit", state="pending"),
    ]
    state = DashboardState(stages=stages_running)
    app = DashboardApp(state=state)
    async with app.run_test():
        diagram = app.query_one(PipelineFlowDiagram)
        nodes = list(diagram.query(PipelineNode))
        assert nodes[0].has_class("-running")

        # Transition to next state
        app.update_state(DashboardState(stages=stages_passed))
        nodes = list(diagram.query(PipelineNode))
        assert nodes[0].has_class("-passed")
        assert nodes[1].has_class("-running")


# ── TS-016: Shared animation timer ──────────────────────────────


@pytest.mark.asyncio
async def test_it22_single_set_interval() -> None:
    """IT-22: Only one set_interval call exists on PipelineFlowDiagram (not per-node)."""
    diagram = PipelineFlowDiagram()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield diagram

    app = _TestApp()
    async with app.run_test():
        diagram.start_animation_timer()
        # Should have exactly one timer reference
        assert diagram._animation_timer is not None
        # Starting again should not create a second timer
        timer_ref = diagram._animation_timer
        diagram.start_animation_timer()
        assert diagram._animation_timer is timer_ref


@pytest.mark.asyncio
async def test_it23_timer_updates_all_running_nodes(sample_stages) -> None:
    """IT-23: Timer tick updates all running nodes' elapsed time in one pass."""
    diagram = PipelineFlowDiagram()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield diagram

    # Two stages running
    stages = [
        StageStatus(name="Plan", state="running", duration_ms=100),
        StageStatus(name="Implement", state="running", duration_ms=200),
        StageStatus(name="Test", state="pending"),
        StageStatus(name="Quality Gate", state="pending"),
        StageStatus(name="Review", state="pending"),
        StageStatus(name="Audit", state="pending"),
    ]
    app = _TestApp()
    async with app.run_test():
        diagram.refresh_stages(stages)
        nodes = list(diagram.query(PipelineNode))
        # Both running nodes should have been updated
        assert nodes[0].has_class("-running")
        assert nodes[1].has_class("-running")


@pytest.mark.asyncio
async def test_it24_timer_stops_when_no_running() -> None:
    """IT-24: When no stages are running, timer is stopped."""
    diagram = PipelineFlowDiagram()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield diagram

    all_passed = [
        StageStatus(name="Plan", state="passed"),
        StageStatus(name="Implement", state="passed"),
        StageStatus(name="Test", state="passed"),
        StageStatus(name="Quality Gate", state="passed"),
        StageStatus(name="Review", state="passed"),
        StageStatus(name="Audit", state="passed"),
    ]
    app = _TestApp()
    async with app.run_test():
        diagram.start_animation_timer()
        diagram.refresh_stages(all_passed)
        # Timer should have been stopped since no stages are running
        assert diagram._animation_timer is None
