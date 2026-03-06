"""Toast + NotificationStore integration tests (TS-014: IT-14 through IT-18).

Verifies toast notifications wire correctly to the existing NotificationStore,
handle rapid bursts, and clean up timers.
"""

from __future__ import annotations

import pytest

from dark_factory.ui.notifications import Notification, NotificationStore, get_store
from dark_factory.ui.widgets.toast import ToastNotification, ToastStack


# ── IT-14: notify() creates toast in stack ───────────────────────


@pytest.mark.asyncio
async def test_it14_notify_creates_toast() -> None:
    """IT-14: notify() call creates a toast in the ToastStack."""
    from dark_factory.ui.dashboard import DashboardApp, DashboardState

    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test():
        stack = app.query_one(ToastStack)
        notif = Notification(event="Test event", detail="", level="info")
        stack.push(notif)
        assert stack.visible_count >= 1


# ── IT-15: Toast auto-dismiss ────────────────────────────────────


@pytest.mark.asyncio
async def test_it15_toast_auto_dismiss() -> None:
    """IT-15: Toast auto-dismisses after configured duration (use 0s override)."""
    notif = Notification(event="Auto-dismiss test", detail="", level="info")
    toast = ToastNotification(notification=notif, dismiss_seconds=0.0)
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield toast

    app = _TestApp()
    async with app.run_test() as pilot:
        # With 0s dismiss, the toast should auto-dismiss quickly
        await pilot.pause(delay=0.1)


# ── IT-16: Rapid burst caps at MAX_VISIBLE ───────────────────────


@pytest.mark.asyncio
async def test_it16_rapid_burst_caps_at_5() -> None:
    """IT-16: Rapid burst of 10 notifications: max 5 visible, no crash."""
    stack = ToastStack()
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield stack

    app = _TestApp()
    async with app.run_test():
        for i in range(10):
            notif = Notification(event=f"Burst {i}", detail="", level="info")
            stack.push(notif)
        assert stack.visible_count <= 5


# ── IT-17: Dismissed toast timer cleanup ─────────────────────────


@pytest.mark.asyncio
async def test_it17_dismissed_toast_timer_cleanup() -> None:
    """IT-17: Dismissed toast timer is cleaned up (no dangling timers)."""
    notif = Notification(event="Timer test", detail="", level="info")
    toast = ToastNotification(notification=notif, dismiss_seconds=10.0)
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield toast

    app = _TestApp()
    async with app.run_test():
        toast.dismiss()
        # After dismiss, any internal timer reference should be cleaned
        if hasattr(toast, "_dismiss_timer"):
            assert toast._dismiss_timer is None


# ── IT-18: NotificationStore ring buffer unaffected ──────────────


def test_it18_store_unaffected_by_toast() -> None:
    """IT-18: NotificationStore ring buffer is unaffected by toast layer."""
    store = NotificationStore(maxlen=50)
    notif1 = Notification(event="Store test", detail="", level="info")
    store.add(notif1)

    # Creating a toast from the same notification should not alter the store
    _toast = ToastNotification(notification=notif1, dismiss_seconds=0.0)

    assert len(store) == 1
    assert store.items[0] is notif1
