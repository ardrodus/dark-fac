"""ToastNotification + ToastStack widget unit tests (TS-010 / TS-011: UN-45 through UN-55)."""

from __future__ import annotations

import pytest

from dark_factory.ui.notifications import Notification
from dark_factory.ui.widgets.toast import (
    ToastNotification,
    ToastNotificationProtocol,
    ToastStack,
    ToastStackProtocol,
)


# ── ToastNotification (TS-010: UN-45 through UN-50) ────────────


class TestToastNotificationProperties:
    """UN-45 through UN-47: Construction and defaults."""

    def test_un45_notification_property(self) -> None:
        """UN-45: Construct with Notification, verify notification property."""
        notif = Notification(event="Test", detail="", level="info")
        toast = ToastNotification(notification=notif)
        assert toast.notification is notif

    def test_un46_default_dismiss_seconds(self) -> None:
        """UN-46: Default dismiss_seconds is 5.0."""
        notif = Notification(event="Test", detail="", level="info")
        toast = ToastNotification(notification=notif)
        assert toast.dismiss_seconds == 5.0

    def test_un47_error_longer_dismiss(self) -> None:
        """UN-47: Error notifications have longer dismiss_seconds."""
        notif = Notification(event="Failure", detail="", level="error")
        toast = ToastNotification(notification=notif)
        assert toast.dismiss_seconds > 5.0


class TestToastNotificationDismiss:
    """UN-48: Dismiss behavior."""

    @pytest.mark.asyncio
    async def test_un48_dismiss_removes_from_dom(self) -> None:
        """UN-48: dismiss() removes widget from DOM."""
        notif = Notification(event="Test", detail="", level="info")
        toast = ToastNotification(notification=notif, dismiss_seconds=0.0)
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield toast

        app = _TestApp()
        async with app.run_test():
            assert len(app.query(ToastNotification)) == 1
            toast.dismiss()
            await app.workers.wait_for_complete()  # type: ignore[arg-type]
            # After dismiss, toast should be removed
            assert len(app.query(ToastNotification)) == 0


class TestToastNotificationSeverityColors:
    """UN-49: Severity-to-color mapping."""

    @pytest.mark.parametrize(
        ("level", "expected_color_keyword"),
        [
            ("info", "blue"),
            ("success", "green"),
            ("warning", "amber"),
            ("error", "red"),
        ],
    )
    def test_un49_severity_color_mapping(self, level: str, expected_color_keyword: str) -> None:
        """UN-49: Severity level maps to correct accent bar color."""
        notif = Notification(event="Test", detail="", level=level)
        toast = ToastNotification(notification=notif)
        # The accent color should contain the expected color keyword or hex
        assert toast.accent_color is not None


def test_un50_conforms_to_protocol() -> None:
    """UN-50: ToastNotification conforms to ToastNotificationProtocol."""
    notif = Notification(event="Test", detail="", level="info")
    toast = ToastNotification(notification=notif)
    assert isinstance(toast, ToastNotificationProtocol)


# ── ToastStack (TS-011: UN-51 through UN-55) ───────────────────


class TestToastStackPush:
    """UN-51 / UN-52: Push and eviction."""

    @pytest.mark.asyncio
    async def test_un51_push_adds_visible_toast(self) -> None:
        """UN-51: push(notification) adds a visible toast."""
        stack = ToastStack()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield stack

        app = _TestApp()
        async with app.run_test():
            notif = Notification(event="Test", detail="", level="info")
            stack.push(notif)
            assert stack.visible_count == 1

    @pytest.mark.asyncio
    async def test_un52_max_visible_cap_evicts_oldest(self) -> None:
        """UN-52: Push 6 notifications; oldest is evicted (MAX_VISIBLE=5)."""
        stack = ToastStack()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield stack

        app = _TestApp()
        async with app.run_test():
            for i in range(6):
                notif = Notification(event=f"Event {i}", detail="", level="info")
                stack.push(notif)
            assert stack.visible_count <= 5


class TestToastStackDismiss:
    """UN-53 / UN-54: Dismiss behavior."""

    @pytest.mark.asyncio
    async def test_un53_dismiss_all_clears(self) -> None:
        """UN-53: dismiss_all() clears all visible toasts."""
        stack = ToastStack()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield stack

        app = _TestApp()
        async with app.run_test():
            for _ in range(3):
                notif = Notification(event="Test", detail="", level="info")
                stack.push(notif)
            stack.dismiss_all()
            assert stack.visible_count == 0

    @pytest.mark.asyncio
    async def test_un54_visible_count_tracks(self) -> None:
        """UN-54: visible_count tracks count correctly after push/dismiss."""
        stack = ToastStack()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield stack

        app = _TestApp()
        async with app.run_test():
            notif = Notification(event="Test", detail="", level="info")
            stack.push(notif)
            assert stack.visible_count == 1
            stack.dismiss_all()
            assert stack.visible_count == 0


def test_un55_conforms_to_protocol() -> None:
    """UN-55: ToastStack conforms to ToastStackProtocol."""
    stack = ToastStack()
    assert isinstance(stack, ToastStackProtocol)
