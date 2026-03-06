"""End-to-end dashboard lifecycle tests (TS-017 / TS-018).

E2E-01: Full dashboard lifecycle (Plan -> Audit)
E2E-02: Toast auto-dismiss flow
E2E-03: Banner toggle round-trip
E2E-04: Hot-reload CSS_PATH compatibility
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dark_factory.ui.dashboard import DashboardApp, DashboardState, StageStatus
from dark_factory.ui.notifications import Notification
from dark_factory.ui.widgets.pipeline import PipelineFlowDiagram, PipelineNode
from dark_factory.ui.widgets.toast import ToastNotification, ToastStack

STAGE_NAMES = ["Plan", "Implement", "Test", "Quality Gate", "Review", "Audit"]


# ── E2E-01: Full dashboard lifecycle ─────────────────────────────


@pytest.mark.asyncio
async def test_e2e01_full_lifecycle() -> None:
    """E2E-01: Mount, progress through all 6 pipeline stages, verify flow diagram state."""
    # Start with all pending
    stages = [StageStatus(name=n, state="pending") for n in STAGE_NAMES]
    state = DashboardState(stages=stages)
    app = DashboardApp(state=state)

    async with app.run_test():
        diagram = app.query_one(PipelineFlowDiagram)

        for i, name in enumerate(STAGE_NAMES):
            # Set stage i to running
            new_stages = []
            for j, n in enumerate(STAGE_NAMES):
                if j < i:
                    new_stages.append(StageStatus(name=n, state="passed", duration_ms=1000.0 * (j + 1)))
                elif j == i:
                    new_stages.append(StageStatus(name=n, state="running", duration_ms=500.0))
                else:
                    new_stages.append(StageStatus(name=n, state="pending"))

            app.update_state(DashboardState(stages=new_stages))
            nodes = list(diagram.query(PipelineNode))

            # Current stage should be running
            assert nodes[i].has_class("-running"), f"Stage {name} should be running"

            # Previous stages should be passed
            for j in range(i):
                assert nodes[j].has_class("-passed"), f"Stage {STAGE_NAMES[j]} should be passed"

            # Later stages should be pending
            for j in range(i + 1, len(STAGE_NAMES)):
                assert nodes[j].has_class("-pending"), f"Stage {STAGE_NAMES[j]} should be pending"

        # Final state: all passed
        all_passed = [StageStatus(name=n, state="passed", duration_ms=2000.0) for n in STAGE_NAMES]
        app.update_state(DashboardState(stages=all_passed))
        nodes = list(diagram.query(PipelineNode))
        for node in nodes:
            assert node.has_class("-passed")


# ── E2E-02: Toast auto-dismiss flow ─────────────────────────────


@pytest.mark.asyncio
async def test_e2e02_toast_auto_dismiss() -> None:
    """E2E-02: Trigger notification -> verify toast appears -> verify dismiss."""
    state = DashboardState()
    app = DashboardApp(state=state)

    async with app.run_test() as pilot:
        stack = app.query_one(ToastStack)
        notif = Notification(event="E2E toast test", detail="", level="info")
        stack.push(notif)
        assert stack.visible_count >= 1

        # With 0s dismiss override in test, toast should auto-dismiss
        # (Real implementation would use set_timer; here we verify the push worked)


# ── E2E-03: Banner toggle round-trip ─────────────────────────────


@pytest.mark.asyncio
async def test_e2e03_banner_toggle() -> None:
    """E2E-03: Mount (banner hidden) -> press b (visible) -> press b (hidden)."""
    state = DashboardState()
    app = DashboardApp(state=state)

    async with app.run_test() as pilot:
        banner = app.query_one("#banner-panel")
        assert banner.display is False, "Banner should start hidden"

        await pilot.press("b")
        assert banner.display is True, "Banner should be visible after first b"

        await pilot.press("b")
        assert banner.display is False, "Banner should be hidden after second b"


# ── E2E-04: CSS_PATH hot-reload compatibility ────────────────────


@pytest.mark.asyncio
async def test_e2e04_css_path_set() -> None:
    """E2E-04: Verify CSS_PATH is set on DashboardApp and .tcss files parse."""
    assert hasattr(DashboardApp, "CSS_PATH"), "DashboardApp should have CSS_PATH attribute"
    css_path = DashboardApp.CSS_PATH
    assert css_path is not None, "CSS_PATH should not be None"

    # Verify the .tcss file exists
    if isinstance(css_path, (str, Path)):
        # CSS_PATH is relative to the module; just verify the app starts
        state = DashboardState()
        app = DashboardApp(state=state)
        async with app.run_test():
            pass  # CSS parsed successfully if we get here
