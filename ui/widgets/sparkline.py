"""Sparkline — inline Unicode block-character chart."""

from __future__ import annotations

from collections import deque
from typing import Protocol, runtime_checkable

from textual.widgets import Static

_BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


@runtime_checkable
class SparklineProtocol(Protocol):
    """Protocol for Sparkline conformance."""

    @property
    def data(self) -> tuple[float, ...]: ...
    def push(self, value: float) -> None: ...
    def clear(self) -> None: ...


class Sparkline(Static):
    """Renders inline Unicode block characters for data visualization.

    Data is bounded by a ``deque(maxlen=100)`` to prevent memory growth.
    """

    def __init__(self, maxlen: int = 100, **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self._data: deque[float] = deque(maxlen=maxlen)

    @property
    def data(self) -> tuple[float, ...]:
        """Return current data values."""
        return tuple(self._data)

    def push(self, value: float) -> None:
        """Add a data point and re-render."""
        self._data.append(value)
        self._render()

    def clear(self) -> None:
        """Empty all data."""
        self._data.clear()
        self.update("")

    def _render(self) -> None:
        if not self._data:
            self.update("")
            return
        lo = min(self._data)
        hi = max(self._data)
        span = hi - lo if hi != lo else 1.0
        chars = []
        for v in self._data:
            idx = int((v - lo) / span * (len(_BLOCKS) - 1))
            chars.append(_BLOCKS[idx])
        self.update("".join(chars))
