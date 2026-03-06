"""ElapsedTimer — displays formatted elapsed time."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from textual.widgets import Static


def _format_elapsed(ms: float) -> str:
    """Format milliseconds as a human-readable duration."""
    seconds = ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


@runtime_checkable
class ElapsedTimerProtocol(Protocol):
    """Protocol for ElapsedTimer conformance."""

    @property
    def display_text(self) -> str: ...
    def update(self, elapsed_ms: float) -> None: ...  # type: ignore[override]
    def reset(self) -> None: ...


class ElapsedTimer(Static):
    """Displays formatted elapsed time, driven by an external caller.

    Call :meth:`update` with ``elapsed_ms`` to refresh the display.
    Fully testable without a wall clock.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__("0.0s", **kwargs)
        self._elapsed_ms: float = 0.0
        self._display_text: str = "0.0s"

    @property
    def display_text(self) -> str:
        """Return the currently displayed time string."""
        return self._display_text

    def update(self, elapsed_ms: float | str = "") -> None:  # type: ignore[override]
        """Update the displayed elapsed time.

        Accepts either a float (milliseconds) for elapsed time updates,
        or a string for Textual's internal Static.update() calls.
        """
        if isinstance(elapsed_ms, (int, float)):
            self._elapsed_ms = float(elapsed_ms)
            self._display_text = _format_elapsed(self._elapsed_ms)
            super().update(self._display_text)
        else:
            super().update(elapsed_ms)

    def update_elapsed(self, elapsed_ms: float) -> None:
        """Alias for update() with float — used by PipelineNode."""
        self.update(elapsed_ms)

    def reset(self) -> None:
        """Reset the display to ``0.0s``."""
        self._elapsed_ms = 0.0
        self._display_text = "0.0s"
        super().update("0.0s")
