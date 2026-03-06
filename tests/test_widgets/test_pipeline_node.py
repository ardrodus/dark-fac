"""PipelineNode widget unit tests (TS-002: UN-01 through UN-12)."""

from __future__ import annotations

import pytest

from dark_factory.ui.widgets.pipeline import PipelineNode, PipelineNodeProtocol


# ── Construction & defaults ─────────────────────────────────────


class TestPipelineNodeConstruction:
    """UN-01 / UN-02: Construction and default state."""

    def test_un01_stage_name_property(self) -> None:
        """UN-01: Construct PipelineNode with stage name, verify stage_name property."""
        node = PipelineNode(stage_name="Plan")
        assert node.stage_name == "Plan"

    @pytest.mark.asyncio
    async def test_un02_default_state_is_pending(self) -> None:
        """UN-02: Default state is 'pending', verify CSS class -pending applied."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            assert node.has_class("-pending")


# ── State CSS classes ───────────────────────────────────────────


class TestPipelineNodeStates:
    """UN-03 through UN-06: CSS class application for each state."""

    @pytest.mark.asyncio
    async def test_un03_running_state_css(self) -> None:
        """UN-03: update_state('running') applies -running CSS class."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            node.update_state("running")
            assert node.has_class("-running")
            assert not node.has_class("-pending")

    @pytest.mark.asyncio
    async def test_un04_passed_state_css_and_checkmark(self) -> None:
        """UN-04: update_state('passed') applies -passed CSS class and checkmark icon."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            node.update_state("passed")
            assert node.has_class("-passed")
            # Checkmark icon should be present in rendered content
            assert "\u2714" in node.render_content()

    @pytest.mark.asyncio
    async def test_un05_failed_state_css_and_x_icon(self) -> None:
        """UN-05: update_state('failed') applies -failed CSS class and X icon."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            node.update_state("failed")
            assert node.has_class("-failed")
            assert "\u2718" in node.render_content()

    @pytest.mark.asyncio
    async def test_un06_skipped_state_css(self) -> None:
        """UN-06: update_state('skipped') applies -skipped CSS class."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            node.update_state("skipped")
            assert node.has_class("-skipped")


# ── Elapsed time formatting ─────────────────────────────────────


class TestPipelineNodeElapsed:
    """UN-07 through UN-09: Elapsed time formatting."""

    @pytest.mark.asyncio
    async def test_un07_elapsed_1500ms(self) -> None:
        """UN-07: update_elapsed(1500) renders '1.5s' under node."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            node.update_elapsed(1500)
            content = node.render_content()
            assert "1.5s" in content

    @pytest.mark.asyncio
    async def test_un08_elapsed_0ms(self) -> None:
        """UN-08: update_elapsed(0) renders '0.0s'."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            node.update_elapsed(0)
            content = node.render_content()
            assert "0.0s" in content

    @pytest.mark.asyncio
    async def test_un09_elapsed_65000ms(self) -> None:
        """UN-09: update_elapsed(65000) renders '1m 5s' (>60s formatting)."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            node.update_elapsed(65000)
            content = node.render_content()
            assert "1m" in content and "5s" in content


# ── State transitions ───────────────────────────────────────────


class TestPipelineNodeTransitions:
    """UN-10 / UN-11: State transition sequences."""

    @pytest.mark.asyncio
    async def test_un10_pending_running_passed(self) -> None:
        """UN-10: pending -> running -> passed transition, verify class changes."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            assert node.has_class("-pending")
            node.update_state("running")
            assert node.has_class("-running")
            assert not node.has_class("-pending")
            node.update_state("passed")
            assert node.has_class("-passed")
            assert not node.has_class("-running")

    @pytest.mark.asyncio
    async def test_un11_pending_running_failed(self) -> None:
        """UN-11: pending -> running -> failed transition, verify class changes."""
        node = PipelineNode(stage_name="Plan")
        from textual.app import App, ComposeResult

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                yield node

        app = _TestApp()
        async with app.run_test():
            assert node.has_class("-pending")
            node.update_state("running")
            assert node.has_class("-running")
            node.update_state("failed")
            assert node.has_class("-failed")
            assert not node.has_class("-running")


# ── Protocol conformance ────────────────────────────────────────


def test_un12_conforms_to_protocol() -> None:
    """UN-12: PipelineNode conforms to PipelineNodeProtocol via isinstance check."""
    node = PipelineNode(stage_name="Plan")
    assert isinstance(node, PipelineNodeProtocol)
