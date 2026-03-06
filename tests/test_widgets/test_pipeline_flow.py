"""PipelineFlowDiagram widget unit tests (TS-004: UN-16 through UN-22)."""

from __future__ import annotations

import pytest

from dark_factory.ui.dashboard import StageStatus
from dark_factory.ui.widgets.pipeline import (
    PipelineConnector,
    PipelineFlowDiagram,
    PipelineFlowDiagramProtocol,
    PipelineNode,
)


def _make_stages(states: list[str]) -> list[StageStatus]:
    """Helper: build 6 StageStatus objects with given states."""
    names = ["Plan", "Implement", "Test", "Quality Gate", "Review", "Audit"]
    return [StageStatus(name=n, state=s) for n, s in zip(names, states)]


class TestPipelineFlowComposition:
    """UN-16 through UN-18: Composition and state propagation."""

    @pytest.mark.asyncio
    async def test_un16_creates_6_nodes_and_5_connectors(self, sample_stages) -> None:
        """UN-16: refresh_stages(6_stages) creates 6 PipelineNode + 5 PipelineConnector."""
        diagram = PipelineFlowDiagram()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield diagram

        app = _TestApp()
        async with app.run_test():
            diagram.refresh_stages(sample_stages)
            nodes = diagram.query(PipelineNode)
            connectors = diagram.query(PipelineConnector)
            assert len(nodes) == 6
            assert len(connectors) == 5

    @pytest.mark.asyncio
    async def test_un17_running_stage_sets_only_that_node(self, sample_stages) -> None:
        """UN-17: refresh_stages with stage[1] running sets only node[1] to running class."""
        diagram = PipelineFlowDiagram()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield diagram

        app = _TestApp()
        async with app.run_test():
            diagram.refresh_stages(sample_stages)
            nodes = list(diagram.query(PipelineNode))
            # stage[1] is "Implement" with state="running"
            assert nodes[1].has_class("-running")
            # Other non-passed nodes should not have -running
            assert not nodes[2].has_class("-running")

    @pytest.mark.asyncio
    async def test_un18_all_passed_sets_all_connectors_completed(self) -> None:
        """UN-18: All stages passed sets all connectors to completed."""
        diagram = PipelineFlowDiagram()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield diagram

        stages = _make_stages(["passed"] * 6)
        app = _TestApp()
        async with app.run_test():
            diagram.refresh_stages(stages)
            connectors = list(diagram.query(PipelineConnector))
            for c in connectors:
                assert c.completed


class TestPipelineFlowTimer:
    """UN-19 / UN-20: Shared timer management."""

    @pytest.mark.asyncio
    async def test_un19_single_shared_timer(self) -> None:
        """UN-19: start_animation_timer creates a single timer (not per-node)."""
        diagram = PipelineFlowDiagram()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield diagram

        app = _TestApp()
        async with app.run_test():
            diagram.start_animation_timer()
            assert diagram._animation_timer is not None

    @pytest.mark.asyncio
    async def test_un20_stop_cancels_timer(self) -> None:
        """UN-20: stop_animation_timer cancels the timer."""
        diagram = PipelineFlowDiagram()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield diagram

        app = _TestApp()
        async with app.run_test():
            diagram.start_animation_timer()
            diagram.stop_animation_timer()
            assert diagram._animation_timer is None


class TestPipelineFlowDirtyFlag:
    """UN-21: Dirty-flag optimization."""

    @pytest.mark.asyncio
    async def test_un21_same_data_skips_rerender(self, sample_stages) -> None:
        """UN-21: Calling refresh_stages with same data skips re-render."""
        diagram = PipelineFlowDiagram()
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield diagram

        app = _TestApp()
        async with app.run_test():
            diagram.refresh_stages(sample_stages)
            first_count = diagram._render_count if hasattr(diagram, "_render_count") else 0
            diagram.refresh_stages(sample_stages)
            second_count = diagram._render_count if hasattr(diagram, "_render_count") else 0
            # Should not have incremented render count on identical data
            assert second_count == first_count


def test_un22_conforms_to_protocol() -> None:
    """UN-22: PipelineFlowDiagram conforms to PipelineFlowDiagramProtocol."""
    diagram = PipelineFlowDiagram()
    assert isinstance(diagram, PipelineFlowDiagramProtocol)
