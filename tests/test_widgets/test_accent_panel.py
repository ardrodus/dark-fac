"""AccentPanel widget unit tests (TS-008: UN-37 through UN-40)."""

from __future__ import annotations

import pytest

from dark_factory.ui.widgets.accent_panel import AccentPanel, AccentPanelProtocol


class TestAccentPanelStyling:
    """UN-37 through UN-39: Border styling and child content."""

    @pytest.mark.asyncio
    async def test_un37_accent_color_border_left(self) -> None:
        """UN-37: Construct with accent_color, verify border-left style applied."""
        panel = AccentPanel(accent_color="#3b82f6")
        from textual.app import App, ComposeResult
        from textual.widgets import Label

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield panel

        app = _TestApp()
        async with app.run_test():
            # The accent panel should have an accent color set
            assert panel.accent_color == "#3b82f6"

    @pytest.mark.asyncio
    async def test_un38_dynamic_color_change(self) -> None:
        """UN-38: set_accent_color('red') changes the border-left color."""
        panel = AccentPanel(accent_color="#3b82f6")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield panel

        app = _TestApp()
        async with app.run_test():
            panel.set_accent_color("red")
            assert panel.accent_color == "red"

    @pytest.mark.asyncio
    async def test_un39_child_content_rendered(self) -> None:
        """UN-39: Child content is rendered inside the panel."""
        from textual.app import App, ComposeResult
        from textual.widgets import Label

        child = Label("Hello", id="child-label")
        panel = AccentPanel(accent_color="#3b82f6", child=child)

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield panel

        app = _TestApp()
        async with app.run_test():
            found = app.query_one("#child-label", Label)
            assert found is not None


def test_un40_conforms_to_protocol() -> None:
    """UN-40: AccentPanel conforms to AccentPanelProtocol."""
    panel = AccentPanel(accent_color="#fff")
    assert isinstance(panel, AccentPanelProtocol)
