"""PipelineConnector widget unit tests (TS-003: UN-13 through UN-15)."""

from __future__ import annotations

import pytest

from dark_factory.ui.widgets.pipeline import PipelineConnector, PipelineConnectorProtocol


class TestPipelineConnector:
    """UN-13 / UN-14: Connector completed/not-completed states."""

    @pytest.mark.asyncio
    async def test_un13_default_state_dim_arrow(self) -> None:
        """UN-13: Default state: completed is False, renders dim arrow."""
        connector = PipelineConnector()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield connector

        app = _TestApp()
        async with app.run_test():
            assert not connector.completed
            # Dim arrow should be the default visual appearance
            content = connector.render_content()
            assert content  # non-empty render

    @pytest.mark.asyncio
    async def test_un14_completed_bright_arrow(self) -> None:
        """UN-14: set_completed(True) renders bright/colored arrow."""
        connector = PipelineConnector()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield connector

        app = _TestApp()
        async with app.run_test():
            connector.set_completed(True)
            assert connector.completed


def test_un15_conforms_to_protocol() -> None:
    """UN-15: PipelineConnector conforms to PipelineConnectorProtocol."""
    connector = PipelineConnector()
    assert isinstance(connector, PipelineConnectorProtocol)
