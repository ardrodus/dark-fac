"""AccentPanel — reusable container with a colored left border bar."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from textual.app import ComposeResult
from textual.widgets import Static


@runtime_checkable
class AccentPanelProtocol(Protocol):
    """Protocol for AccentPanel conformance."""

    @property
    def accent_color(self) -> str: ...
    def set_accent_color(self, color: str) -> None: ...


class AccentPanel(Static):
    """Container widget with a colored left accent bar.

    The accent color is applied via inline styling on the border-left
    property.  Use ``set_accent_color()`` to update dynamically.
    """

    def __init__(
        self,
        *args: Any,
        accent_color: str = "#7c3aed",
        child: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._accent_color = accent_color
        self._child = child

    @property
    def accent_color(self) -> str:
        """Return the current accent color."""
        return self._accent_color

    def compose(self) -> ComposeResult:
        if self._child is not None:
            yield self._child

    def on_mount(self) -> None:
        self.styles.border_left = ("thick", self._accent_color)

    def set_accent_color(self, color: str) -> None:
        """Update the left accent bar color."""
        self._accent_color = color
        try:
            self.styles.border_left = ("thick", color)
        except Exception:  # noqa: BLE001
            pass
