"""Timestamped event notifications for the Dark Factory dashboard.

Ports ``src/notify.sh`` — ring-buffer store, ``notify()`` convenience
function, Textual widget, and stderr console fallback.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from textual.app import ComposeResult
from textual.widgets import Label, RichLog, Static

from factory.ui.theme import PILLARS, THEME, format_relative_time

logger = logging.getLogger(__name__)

_LEVEL_COLOUR: dict[str, str] = {
    "info": THEME.info, "success": THEME.success,
    "error": THEME.error, "warning": THEME.warning,
}
_LEVEL_ICON: dict[str, str] = {
    "info": "\u2139",   # ℹ  info circle
    "success": "\u2714",  # ✔  checkmark
    "error": "\u2718",    # ✘  X mark
    "warning": "\u26a0",  # ⚠  warning triangle
}


@dataclass(frozen=True, slots=True)
class Notification:
    """A single timestamped notification."""

    event: str
    detail: str
    level: str  # "info" | "success" | "error" | "warning"
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%H:%M:%S"),
    )
    created_at: float = field(default_factory=time.monotonic)


class NotificationStore:
    """Thread-safe ring buffer of the last *maxlen* notifications."""

    def __init__(self, maxlen: int = 50) -> None:
        self._buf: deque[Notification] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, n: Notification) -> None:
        with self._lock:
            self._buf.append(n)

    @property
    def items(self) -> tuple[Notification, ...]:
        with self._lock:
            return tuple(self._buf)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


_store = NotificationStore()


def notify(event: str, detail: str = "", level: str = "info") -> None:
    """Queue a notification and print a timestamped line to stderr."""
    n = Notification(event=event, detail=detail, level=level)
    _store.add(n)
    print(f"[{n.timestamp}] {level.upper()}: {event} {detail}".rstrip(), file=sys.stderr)


def get_store() -> NotificationStore:
    """Return the module-level notification store."""
    return _store


class NotificationPanel(Static):
    """Textual widget showing the most recent notifications."""

    def compose(self) -> ComposeResult:
        yield Label(f"[b][{PILLARS.crucible}]\u25a0[/] Notifications[/b]")
        yield RichLog(id="notif-log", highlight=True, markup=True, max_lines=50)

    def refresh_notifications(self, notifications: tuple[Notification, ...]) -> None:
        """Rewrite the log with the latest notifications."""
        log: RichLog = self.query_one("#notif-log", RichLog)
        log.clear()
        now = time.monotonic()
        for n in notifications:
            clr = _LEVEL_COLOUR.get(n.level, THEME.text)
            icon = _LEVEL_ICON.get(n.level, "?")
            age = format_relative_time(now - n.created_at)
            msg = f"[{THEME.text_muted}]{age:>8}[/] [{clr}]{icon}[/] {n.event} {n.detail}"
            log.write(msg.rstrip())
