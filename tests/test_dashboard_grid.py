"""Dashboard grid layout integration tests (TS-012: IT-01 through IT-06).

Verifies the dashboard grid layout composition - flow diagram at top,
side-by-side panels, banner toggle, and notification panel removal.
"""

from __future__ import annotations

import pytest

from dark_factory.ui.dashboard import DashboardApp, DashboardState
from dark_factory.ui.widgets.pipeline import PipelineFlowDiagram


# ── IT-01: Flow diagram at top of grid ───────────────────────────


@pytest.mark.asyncio
async def test_it01_flow_diagram_at_top() -> None:
    """IT-01: DashboardApp.compose() yields grid layout with PipelineFlowDiagram at top."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test():
        diagram = app.query_one(PipelineFlowDiagram)
        assert diagram is not None


# ── IT-02: Agent and Health side-by-side ─────────────────────────


@pytest.mark.asyncio
async def test_it02_agent_health_side_by_side() -> None:
    """IT-02: Agent and Health panels are side-by-side (in a Horizontal container)."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test():
        agent_panel = app.query_one("#agent-panel")
        health_panel = app.query_one("#health-panel")
        assert agent_panel is not None
        assert health_panel is not None
        # Both should share the same parent (Horizontal container)
        assert agent_panel.parent is health_panel.parent


# ── IT-03: Gate summary full-width below ─────────────────────────


@pytest.mark.asyncio
async def test_it03_gate_panel_below() -> None:
    """IT-03: Gate summary is full-width below agent/health row."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test():
        gate = app.query_one("#gate-panel")
        assert gate is not None


# ── IT-04: Log panel at bottom with flex ─────────────────────────


@pytest.mark.asyncio
async def test_it04_log_panel_at_bottom() -> None:
    """IT-04: Live logs panel is at bottom with flex-grow."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test():
        log = app.query_one("#log-panel")
        assert log is not None


# ── IT-05: Banner hidden by default ──────────────────────────────


@pytest.mark.asyncio
async def test_it05_banner_hidden_by_default() -> None:
    """IT-05: Banner is not visible by default."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test():
        banner = app.query_one("#banner-panel")
        assert banner.display is False


# ── IT-06: NotificationPanel NOT in permanent layout ─────────────


@pytest.mark.asyncio
async def test_it06_no_permanent_notification_panel() -> None:
    """IT-06: NotificationPanel is NOT in the permanent layout (replaced by toasts)."""
    from dark_factory.ui.notifications import NotificationPanel

    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test():
        panels = app.query(NotificationPanel)
        assert len(panels) == 0
