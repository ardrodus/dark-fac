"""StatusBadge — compact colored state label with icon."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from textual.widgets import Static

_BADGE_CONFIG: dict[str, tuple[str, str]] = {
    "passed": ("\u2714 PASS", "#22c55e"),
    "pass": ("\u2714 PASS", "#22c55e"),
    "failed": ("\u2718 FAIL", "#ef4444"),
    "fail": ("\u2718 FAIL", "#ef4444"),
    "running": ("\u25b6 RUNNING", "#3b82f6"),
    "pending": ("\u2504 PENDING", "#94a3b8"),
    "skipped": ("\u2500 SKIPPED", "#94a3b8"),
}


@runtime_checkable
class StatusBadgeProtocol(Protocol):
    """Protocol for StatusBadge conformance."""

    def set_status(self, status: str) -> None: ...


class StatusBadge(Static):
    """Compact colored label showing PASS/FAIL/RUNNING/PENDING with icon."""

    def __init__(self, status: str = "pending", **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self._status = status

    def on_mount(self) -> None:
        self._apply()

    def set_status(self, status: str) -> None:
        """Update the badge to reflect a new status."""
        self._status = status
        self._apply()

    def _apply(self) -> None:
        label, color = _BADGE_CONFIG.get(self._status, ("\u2504 PENDING", "#94a3b8"))
        for state in _BADGE_CONFIG:
            self.remove_class(f"-{state}")
        self.add_class(f"-{self._status}")
        self.update(f"[{color}]{label}[/{color}]")
