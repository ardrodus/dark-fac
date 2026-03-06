"""Shared fixtures and mocks for widget tests (TS-020)."""

from __future__ import annotations

import pytest

from dark_factory.ui.dashboard import (
    AgentInfo,
    DashboardState,
    GateSummary,
    HealthStatus,
    ObeliskInvestigation,
    ObeliskStatus,
    StageStatus,
)
from dark_factory.ui.notifications import Notification


# ── Sample data fixtures ────────────────────────────────────────


@pytest.fixture()
def sample_stages() -> list[StageStatus]:
    """6 stages in various states for testing flow diagram."""
    return [
        StageStatus(name="Plan", state="passed", duration_ms=1200),
        StageStatus(name="Implement", state="running", duration_ms=3400),
        StageStatus(name="Test", state="pending", duration_ms=0),
        StageStatus(name="Quality Gate", state="pending", duration_ms=0),
        StageStatus(name="Review", state="pending", duration_ms=0),
        StageStatus(name="Audit", state="pending", duration_ms=0),
    ]


@pytest.fixture()
def sample_agents() -> list[AgentInfo]:
    """Agent info for dashboard panels."""
    return [
        AgentInfo(role="planner", status="idle"),
        AgentInfo(role="implementer", status="active", task="Coding feature X"),
    ]


@pytest.fixture()
def sample_health() -> list[HealthStatus]:
    """Health status for dashboard panels."""
    return [
        HealthStatus(component="engine", healthy=True),
        HealthStatus(component="obelisk", healthy=False, detail="degraded"),
    ]


@pytest.fixture()
def sample_gates() -> list[GateSummary]:
    """Gate summaries for dashboard panels."""
    return [
        GateSummary(name="quality", passed=True, check_count=5, detail="All checks passed"),
        GateSummary(name="review", passed=False, check_count=3, detail="Awaiting review"),
    ]


@pytest.fixture()
def sample_obelisk() -> ObeliskStatus:
    """Obelisk status for dashboard testing."""
    return ObeliskStatus(
        status="watching",
        dark_factory_pid=1234,
        uptime_s=300.0,
        crash_count=0,
        investigations=(),
    )


@pytest.fixture()
def sample_notifications() -> tuple[Notification, ...]:
    """Notification data with all 4 severity levels for toast testing."""
    return (
        Notification(event="Pipeline started", detail="", level="info"),
        Notification(event="Plan stage completed", detail="", level="success"),
        Notification(event="Test stage failed", detail="see logs", level="error"),
        Notification(event="Resource limit approaching", detail="", level="warning"),
    )


@pytest.fixture()
def full_dashboard_state(
    sample_stages: list[StageStatus],
    sample_agents: list[AgentInfo],
    sample_health: list[HealthStatus],
    sample_gates: list[GateSummary],
    sample_notifications: tuple[Notification, ...],
    sample_obelisk: ObeliskStatus,
) -> DashboardState:
    """Complete DashboardState for integration tests."""
    return DashboardState(
        stages=sample_stages,
        agents=sample_agents,
        health=sample_health,
        gate_summaries=sample_gates,
        notifications=sample_notifications,
        obelisk=sample_obelisk,
    )


@pytest.fixture()
def toast_factory():
    """Factory for creating ToastNotification widgets with 0s dismiss for testing."""
    from dark_factory.ui.widgets.toast import ToastNotification

    def _make(notification: Notification, dismiss_seconds: float = 0.0):
        return ToastNotification(notification=notification, dismiss_seconds=dismiss_seconds)

    return _make


# ── Mocks ───────────────────────────────────────────────────────


class MockTimer:
    """Mock for Textual's set_interval/set_timer return value."""

    def __init__(self) -> None:
        self.stopped = False
        self.callbacks: list = []

    def stop(self) -> None:
        self.stopped = True

    def fire(self) -> None:
        for cb in self.callbacks:
            cb()


class MockNotificationStore:
    """Mock for the NotificationStore ring buffer."""

    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    def add(self, notification: Notification) -> None:
        self.notifications.append(notification)

    @property
    def items(self) -> tuple[Notification, ...]:
        return tuple(self.notifications)

    def clear(self) -> None:
        self.notifications.clear()

    def __len__(self) -> int:
        return len(self.notifications)


class MockClock:
    """Injectable clock for elapsed-time testing."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, ms: float) -> None:
        self._now += ms
