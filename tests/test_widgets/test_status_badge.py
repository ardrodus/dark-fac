"""StatusBadge widget unit tests (TS-007: UN-32 through UN-36)."""

from __future__ import annotations

import pytest

from dark_factory.ui.widgets.status_badge import StatusBadge, StatusBadgeProtocol


class TestStatusBadgeStates:
    """UN-32 through UN-35: Status rendering for each state."""

    @pytest.mark.asyncio
    async def test_un32_default_pending_styling(self) -> None:
        """UN-32: Default status renders with pending styling."""
        badge = StatusBadge()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield badge

        app = _TestApp()
        async with app.run_test():
            assert badge.has_class("-pending")

    @pytest.mark.asyncio
    async def test_un33_pass_green_checkmark(self) -> None:
        """UN-33: set_status('pass') applies green/checkmark styling."""
        badge = StatusBadge()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield badge

        app = _TestApp()
        async with app.run_test():
            badge.set_status("pass")
            assert badge.has_class("-pass")

    @pytest.mark.asyncio
    async def test_un34_fail_red_x(self) -> None:
        """UN-34: set_status('fail') applies red/X styling."""
        badge = StatusBadge()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield badge

        app = _TestApp()
        async with app.run_test():
            badge.set_status("fail")
            assert badge.has_class("-fail")

    @pytest.mark.asyncio
    async def test_un35_running_animated(self) -> None:
        """UN-35: set_status('running') applies animated styling."""
        badge = StatusBadge()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield badge

        app = _TestApp()
        async with app.run_test():
            badge.set_status("running")
            assert badge.has_class("-running")


def test_un36_conforms_to_protocol() -> None:
    """UN-36: StatusBadge conforms to StatusBadgeProtocol."""
    badge = StatusBadge()
    assert isinstance(badge, StatusBadgeProtocol)
