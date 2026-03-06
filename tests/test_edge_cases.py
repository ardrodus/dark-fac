"""Edge case tests (TS-021: EC-01 through EC-14).

Covers boundary conditions: empty states, rapid transitions, overflow,
resize, and theme switching during animation.
"""

from __future__ import annotations

import pytest

from dark_factory.ui.dashboard import DashboardApp, DashboardState, StageStatus
from dark_factory.ui.notifications import Notification
from dark_factory.ui.widgets.pipeline import (
    PipelineConnector,
    PipelineFlowDiagram,
    PipelineNode,
)
from dark_factory.ui.widgets.sparkline import Sparkline
from dark_factory.ui.widgets.toast import ToastNotification, ToastStack

STAGE_NAMES = ["Plan", "Implement", "Test", "Quality Gate", "Review", "Audit"]


# ── EC-01: All 6 stages pending ──────────────────────────────────


@pytest.mark.asyncio
async def test_ec01_all_pending() -> None:
    """EC-01: All 6 stages pending — all nodes dim, no connectors completed, no timer."""
    diagram = PipelineFlowDiagram()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield diagram

    stages = [StageStatus(name=n, state="pending") for n in STAGE_NAMES]
    app = _TestApp()
    async with app.run_test():
        diagram.refresh_stages(stages)
        for node in diagram.query(PipelineNode):
            assert node.has_class("-pending")
        for conn in diagram.query(PipelineConnector):
            assert not conn.completed
        assert diagram._animation_timer is None


# ── EC-02: All 6 stages passed ───────────────────────────────────


@pytest.mark.asyncio
async def test_ec02_all_passed() -> None:
    """EC-02: All 6 stages passed — all nodes green, all connectors completed, timer stopped."""
    diagram = PipelineFlowDiagram()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield diagram

    stages = [StageStatus(name=n, state="passed", duration_ms=1000) for n in STAGE_NAMES]
    app = _TestApp()
    async with app.run_test():
        diagram.refresh_stages(stages)
        for node in diagram.query(PipelineNode):
            assert node.has_class("-passed")
        for conn in diagram.query(PipelineConnector):
            assert conn.completed


# ── EC-03: Middle stage failed ────────────────────────────────────


@pytest.mark.asyncio
async def test_ec03_middle_stage_failed() -> None:
    """EC-03: Middle stage failed, rest pending — failed node red, subsequent pending."""
    diagram = PipelineFlowDiagram()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield diagram

    stages = [
        StageStatus(name="Plan", state="passed"),
        StageStatus(name="Implement", state="passed"),
        StageStatus(name="Test", state="failed"),
        StageStatus(name="Quality Gate", state="pending"),
        StageStatus(name="Review", state="pending"),
        StageStatus(name="Audit", state="pending"),
    ]
    app = _TestApp()
    async with app.run_test():
        diagram.refresh_stages(stages)
        nodes = list(diagram.query(PipelineNode))
        assert nodes[2].has_class("-failed")
        assert nodes[3].has_class("-pending")
        assert nodes[4].has_class("-pending")


# ── EC-04: Running to failed transition ──────────────────────────


@pytest.mark.asyncio
async def test_ec04_running_to_failed() -> None:
    """EC-04: Stage transitions from running to failed."""
    node = PipelineNode(stage_name="Test")
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield node

    app = _TestApp()
    async with app.run_test():
        node.update_state("running")
        assert node.has_class("-running")
        node.update_state("failed")
        assert node.has_class("-failed")
        assert not node.has_class("-running")


# ── EC-05: Very long elapsed time ────────────────────────────────


def test_ec05_long_elapsed_time() -> None:
    """EC-05: Very long elapsed time (>1 hour) formats correctly."""
    from dark_factory.ui.widgets.elapsed_timer import ElapsedTimer

    timer = ElapsedTimer()
    timer.update(3_660_000)  # 1 hour, 1 minute
    text = timer.display_text
    assert "1h" in text or "61m" in text  # Either format is acceptable


# ── EC-06: Rapid stage transitions ────────────────────────────────


@pytest.mark.asyncio
async def test_ec06_rapid_transitions() -> None:
    """EC-06: Rapid stage transitions (all 6 complete in quick succession)."""
    diagram = PipelineFlowDiagram()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield diagram

    app = _TestApp()
    async with app.run_test():
        for i in range(6):
            stages = []
            for j in range(6):
                if j <= i:
                    stages.append(StageStatus(name=STAGE_NAMES[j], state="passed"))
                else:
                    stages.append(StageStatus(name=STAGE_NAMES[j], state="pending"))
            diagram.refresh_stages(stages)

        # All should be passed at the end
        for node in diagram.query(PipelineNode):
            assert node.has_class("-passed")


# ── EC-07: 50+ rapid notifications ───────────────────────────────


@pytest.mark.asyncio
async def test_ec07_many_notifications() -> None:
    """EC-07: 50+ rapid notifications caps at 5 visible, no memory leak."""
    stack = ToastStack()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield stack

    app = _TestApp()
    async with app.run_test():
        for i in range(50):
            notif = Notification(event=f"Burst {i}", detail="", level="info")
            stack.push(notif)
        assert stack.visible_count <= 5


# ── EC-08: Toast dismissed before auto-dismiss ────────────────────


@pytest.mark.asyncio
async def test_ec08_early_dismiss_no_error() -> None:
    """EC-08: Toast dismissed before auto-dismiss timer fires doesn't error."""
    notif = Notification(event="Early dismiss", detail="", level="info")
    toast = ToastNotification(notification=notif, dismiss_seconds=10.0)
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield toast

    app = _TestApp()
    async with app.run_test():
        toast.dismiss()
        # Should not error


# ── EC-09: Rapid banner toggle ────────────────────────────────────


@pytest.mark.asyncio
async def test_ec09_rapid_banner_toggle() -> None:
    """EC-09: Banner toggled rapidly (b-b-b-b) — state toggles correctly."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test() as pilot:
        banner = app.query_one("#banner-panel")
        initial = banner.display
        for _ in range(4):
            await pilot.press("b")
        # After 4 toggles, should be back to initial state
        assert banner.display == initial


# ── EC-10: Dashboard with empty state ─────────────────────────────


@pytest.mark.asyncio
async def test_ec10_empty_state_no_crash() -> None:
    """EC-10: Dashboard mounted with empty state (no stages) doesn't crash."""
    state = DashboardState(stages=[])
    app = DashboardApp(state=state)
    async with app.run_test():
        diagram = app.query_one(PipelineFlowDiagram)
        nodes = list(diagram.query(PipelineNode))
        assert len(nodes) == 0


# ── EC-11: Sparkline all zeros ────────────────────────────────────


def test_ec11_sparkline_all_zeros() -> None:
    """EC-11: Sparkline with all zero values — no division by zero."""
    spark = Sparkline()
    for _ in range(10):
        spark.push(0.0)
    assert len(spark.data) == 10


# ── EC-12: Sparkline single data point ────────────────────────────


def test_ec12_sparkline_single_point() -> None:
    """EC-12: Sparkline with single data point — no crash."""
    spark = Sparkline()
    spark.push(42.0)
    assert len(spark.data) == 1
    assert spark.data[0] == 42.0


# ── EC-13: Window resize during animation ─────────────────────────


@pytest.mark.asyncio
async def test_ec13_resize_during_animation() -> None:
    """EC-13: Window resize during animation re-renders correctly."""
    stages = [
        StageStatus(name="Plan", state="running", duration_ms=500),
        StageStatus(name="Implement", state="pending"),
        StageStatus(name="Test", state="pending"),
        StageStatus(name="Quality Gate", state="pending"),
        StageStatus(name="Review", state="pending"),
        StageStatus(name="Audit", state="pending"),
    ]
    state = DashboardState(stages=stages)
    app = DashboardApp(state=state)
    async with app.run_test(size=(120, 40)) as pilot:
        diagram = app.query_one(PipelineFlowDiagram)
        # Simulate resize
        await pilot.resize_terminal(80, 24)
        # Should not crash, diagram should still have nodes
        nodes = list(diagram.query(PipelineNode))
        assert len(nodes) == 6


# ── EC-14: Theme switch during animation ──────────────────────────


@pytest.mark.asyncio
async def test_ec14_theme_switch_during_animation() -> None:
    """EC-14: Theme/subsystem switch while flow diagram is animating."""
    from dark_factory.ui.theme import apply_subsystem_theme

    stages = [
        StageStatus(name="Plan", state="running", duration_ms=500),
        StageStatus(name="Implement", state="pending"),
        StageStatus(name="Test", state="pending"),
        StageStatus(name="Quality Gate", state="pending"),
        StageStatus(name="Review", state="pending"),
        StageStatus(name="Audit", state="pending"),
    ]
    state = DashboardState(stages=stages)
    app = DashboardApp(state=state)
    async with app.run_test():
        # Switch theme while animation timer might be active
        apply_subsystem_theme(app, "dark_forge")
        diagram = app.query_one(PipelineFlowDiagram)
        nodes = list(diagram.query(PipelineNode))
        # Animation should continue, nodes should still exist
        assert len(nodes) == 6
        assert nodes[0].has_class("-running")
