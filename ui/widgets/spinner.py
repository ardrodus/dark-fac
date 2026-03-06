"""AnimatedSpinner — braille-dot spinner driven by a parent timer."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from textual.widgets import Static

_BRAILLE_FRAMES: tuple[str, ...] = (
    "\u280b", "\u2819", "\u2839", "\u2838",
    "\u283c", "\u2834", "\u2826", "\u2827",
    "\u2807", "\u280f",
)


@runtime_checkable
class AnimatedSpinnerProtocol(Protocol):
    """Protocol for AnimatedSpinner conformance."""

    @property
    def current_frame(self) -> str: ...
    def tick(self) -> None: ...


class AnimatedSpinner(Static):
    """Cycles through braille dot patterns on each ``tick()`` call.

    Does NOT own its own ``set_interval`` — the parent widget drives
    the animation by calling :meth:`tick`.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self._frame_idx: int = 0

    @property
    def current_frame(self) -> str:
        """Return the current braille character."""
        return _BRAILLE_FRAMES[self._frame_idx]

    @property
    def frame(self) -> str:
        """Alias for current_frame."""
        return self.current_frame

    def tick(self) -> None:
        """Advance to the next frame and update the display."""
        self._frame_idx = (self._frame_idx + 1) % len(_BRAILLE_FRAMES)
        self.update(self.current_frame)
