"""Toast notification widgets — self-dismissing overlay toasts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from textual.app import ComposeResult
from textual.widgets import Label, Static

from dark_factory.ui.theme import THEME

if TYPE_CHECKING:
    from textual.timer import Timer

    from dark_factory.ui.notifications import Notification

logger = logging.getLogger(__name__)

_LEVEL_ICON: dict[str, str] = {
    "info": "\u2139",
    "success": "\u2714",
    "error": "\u2718",
    "warning": "\u26a0",
}

_LEVEL_COLOR: dict[str, str] = {
    "info": "#3b82f6",
    "success": "#22c55e",
    "error": "#ef4444",
    "warning": "#f59e0b",
}


@runtime_checkable
class ToastNotificationProtocol(Protocol):
    """Protocol for ToastNotification conformance."""

    @property
    def notification(self) -> Any: ...
    @property
    def dismiss_seconds(self) -> float: ...
    @property
    def accent_color(self) -> str: ...
    def dismiss(self) -> None: ...


@runtime_checkable
class ToastStackProtocol(Protocol):
    """Protocol for ToastStack conformance."""

    @property
    def visible_count(self) -> int: ...
    def push(self, notification: Any) -> None: ...
    def dismiss_all(self) -> None: ...


class ToastNotification(Static):
    """A single self-dismissing toast notification.

    Renders with a colored left accent bar per severity.
    Auto-dismisses via ``set_timer``.
    """

    def __init__(
        self,
        notification: Any = None,
        event: str = "",
        detail: str = "",
        level: str = "info",
        dismiss_seconds: float = 5.0,
        **kwargs: object,
    ) -> None:
        super().__init__("", **kwargs)
        self._notification = notification
        if notification is not None:
            self._event = notification.event
            self._detail = notification.detail
            self._level = notification.level
        else:
            self._event = event
            self._detail = detail
            self._level = level
        # Error notifications get longer dismiss time
        if self._level == "error" and dismiss_seconds <= 5.0:
            self._dismiss_seconds = 10.0
        else:
            self._dismiss_seconds = dismiss_seconds
        self._dismiss_timer: Timer | None = None
        self._accent_color = _LEVEL_COLOR.get(self._level, THEME.text)

    @property
    def notification(self) -> Any:
        """Return the underlying Notification object."""
        return self._notification

    @property
    def dismiss_seconds(self) -> float:
        """Return the auto-dismiss timeout in seconds."""
        return self._dismiss_seconds

    @property
    def accent_color(self) -> str:
        """Return the severity-based accent color."""
        return self._accent_color

    def compose(self) -> ComposeResult:
        icon = _LEVEL_ICON.get(self._level, "?")
        color = _LEVEL_COLOR.get(self._level, THEME.text)
        text = f"[{color}]{icon}[/{color}] {self._event}"
        if self._detail:
            text += f"\n[{THEME.text_muted}]{self._detail}[/{THEME.text_muted}]"
        yield Label(text)

    def on_mount(self) -> None:
        self.add_class(f"-{self._level}")
        if self._dismiss_seconds > 0:
            self._dismiss_timer = self.set_timer(self._dismiss_seconds, self.dismiss)

    def dismiss(self) -> None:
        """Remove this toast and clean up the timer."""
        if self._dismiss_timer is not None:
            self._dismiss_timer.stop()
            self._dismiss_timer = None
        # Move to a non-queryable state immediately
        if self.parent is not None:
            try:
                self.parent._nodes._remove(self)  # type: ignore[union-attr]
            except (ValueError, AttributeError):
                pass
        self.remove()


class ToastStack(Static):
    """Overlay container for toast notifications.

    Stacked in the top-right corner with a cap of 5 visible.
    Backed by the existing ``NotificationStore``.
    """

    MAX_VISIBLE = 5

    def __init__(self, dismiss_seconds: float = 5.0, **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self._dismiss_seconds = dismiss_seconds
        self._seen_ids: set[float] = set()

    @property
    def visible_count(self) -> int:
        """Return the number of currently visible toasts."""
        return len([c for c in self.children if isinstance(c, ToastNotification)])

    def push(self, notification: Any) -> None:
        """Add a toast for a Notification instance."""
        nid = notification.created_at
        if nid in self._seen_ids:
            return
        self._seen_ids.add(nid)

        toast = ToastNotification(
            notification=notification,
            dismiss_seconds=self._dismiss_seconds,
        )
        self.mount(toast)

        # Evict oldest if over cap
        children = [c for c in self.children if isinstance(c, ToastNotification)]
        while len(children) > self.MAX_VISIBLE:
            oldest = children.pop(0)
            oldest.dismiss()
            children = [c for c in self.children if isinstance(c, ToastNotification)]

    def push_notification(self, notification: Any) -> None:
        """Alias for push() — used by dashboard integration."""
        self.push(notification)

    def dismiss_all(self) -> None:
        """Clear all visible toasts."""
        for child in list(self.children):
            if isinstance(child, ToastNotification):
                child.dismiss()
