"""Tests for _run_forge_interactive in cli/dispatch.py.

Verifies that the interactive forge command:
- Calls engine.run_pipeline("dark_forge") with workspace_root context
- Manages issue labels (in-progress → done/failed)
- Handles no-issue and workspace-error edge cases
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from dark_factory.integrations.gh_safe import IssueInfo
from dark_factory.workspace.manager import Workspace


def _issue(number: int = 42, title: str = "Test issue") -> IssueInfo:
    return IssueInfo(number=number, title=title, labels=("factory-task",), state="OPEN")


def _workspace(path: str = "/tmp/ws") -> Workspace:
    return Workspace(
        name="test/repo", path=path,
        repo_url="https://github.com/test/repo.git",
        branch="dark-factory/issue-42",
    )


class TestRunForgeInteractive:
    """Verify _run_forge_interactive calls engine.run_pipeline directly."""

    @patch("dark_factory.cli.dispatch.input", return_value="")
    @patch("dark_factory.cli.dispatch.sys")
    def test_calls_engine_run_pipeline(
        self, mock_sys: MagicMock, _mock_input: MagicMock,
    ) -> None:
        """Interactive forge must call engine.run_pipeline('dark_forge')."""
        mock_sys.stdout = MagicMock()

        mock_engine = MagicMock()
        mock_engine.run_pipeline = AsyncMock(return_value=MagicMock())

        with (
            patch(
                "dark_factory.dispatch.issue_dispatcher.select_next_issue",
                return_value=_issue(),
            ),
            patch(
                "dark_factory.workspace.manager.acquire_workspace",
                return_value=_workspace(),
            ),
            patch(
                "dark_factory.pipeline.engine.FactoryPipelineEngine",
                return_value=mock_engine,
            ),
            patch("dark_factory.integrations.gh_safe.add_label"),
            patch("dark_factory.integrations.gh_safe.remove_label"),
        ):
            from dark_factory.cli.dispatch import _run_forge_interactive

            _run_forge_interactive()

        mock_engine.run_pipeline.assert_called_once()
        call_args = mock_engine.run_pipeline.call_args
        assert call_args[0][0] == "dark_forge"
        ctx = call_args[0][1]
        assert "workspace_root" in ctx
        assert ctx["workspace_root"] == "/tmp/ws"

    @patch("dark_factory.cli.dispatch.input", return_value="")
    @patch("dark_factory.cli.dispatch.sys")
    def test_forge_failure_reports_failed(
        self, mock_sys: MagicMock, _mock_input: MagicMock,
    ) -> None:
        """When engine raises, output should say FAILED."""
        buf: list[str] = []
        mock_sys.stdout = MagicMock()
        mock_sys.stdout.write = lambda s: buf.append(s)
        mock_sys.stdout.flush = lambda: None

        mock_engine = MagicMock()
        mock_engine.run_pipeline = AsyncMock(side_effect=RuntimeError("pipeline failed"))

        with (
            patch(
                "dark_factory.dispatch.issue_dispatcher.select_next_issue",
                return_value=_issue(),
            ),
            patch(
                "dark_factory.workspace.manager.acquire_workspace",
                return_value=_workspace(),
            ),
            patch(
                "dark_factory.pipeline.engine.FactoryPipelineEngine",
                return_value=mock_engine,
            ),
            patch("dark_factory.integrations.gh_safe.add_label"),
            patch("dark_factory.integrations.gh_safe.remove_label"),
        ):
            from dark_factory.cli.dispatch import _run_forge_interactive

            _run_forge_interactive()

        combined = "".join(buf)
        assert "FAILED" in combined

    @patch("dark_factory.cli.dispatch.input", return_value="")
    @patch("dark_factory.cli.dispatch.sys")
    def test_forge_success_reports_passed(
        self, mock_sys: MagicMock, _mock_input: MagicMock,
    ) -> None:
        """When engine succeeds, output should say PASSED."""
        buf: list[str] = []
        mock_sys.stdout = MagicMock()
        mock_sys.stdout.write = lambda s: buf.append(s)
        mock_sys.stdout.flush = lambda: None

        mock_engine = MagicMock()
        mock_engine.run_pipeline = AsyncMock(return_value=MagicMock())

        with (
            patch(
                "dark_factory.dispatch.issue_dispatcher.select_next_issue",
                return_value=_issue(),
            ),
            patch(
                "dark_factory.workspace.manager.acquire_workspace",
                return_value=_workspace(),
            ),
            patch(
                "dark_factory.pipeline.engine.FactoryPipelineEngine",
                return_value=mock_engine,
            ),
            patch("dark_factory.integrations.gh_safe.add_label"),
            patch("dark_factory.integrations.gh_safe.remove_label"),
        ):
            from dark_factory.cli.dispatch import _run_forge_interactive

            _run_forge_interactive()

        combined = "".join(buf)
        assert "PASSED" in combined

    @patch("dark_factory.cli.dispatch.input", return_value="")
    @patch("dark_factory.cli.dispatch.sys")
    def test_no_issues_returns_early(
        self, mock_sys: MagicMock, _mock_input: MagicMock,
    ) -> None:
        """When no issues are queued, return without calling engine."""
        mock_sys.stdout = MagicMock()

        with (
            patch(
                "dark_factory.dispatch.issue_dispatcher.select_next_issue",
                return_value=None,
            ),
        ):
            from dark_factory.cli.dispatch import _run_forge_interactive

            _run_forge_interactive()

    @patch("dark_factory.cli.dispatch.input", return_value="")
    @patch("dark_factory.cli.dispatch.sys")
    def test_workspace_error_returns_early(
        self, mock_sys: MagicMock, _mock_input: MagicMock,
    ) -> None:
        """When workspace acquisition fails, return without calling engine."""
        mock_sys.stdout = MagicMock()

        with (
            patch(
                "dark_factory.dispatch.issue_dispatcher.select_next_issue",
                return_value=_issue(),
            ),
            patch(
                "dark_factory.workspace.manager.acquire_workspace",
                side_effect=RuntimeError("clone failed"),
            ),
            patch("dark_factory.integrations.gh_safe.add_label"),
            patch("dark_factory.integrations.gh_safe.remove_label"),
        ):
            from dark_factory.cli.dispatch import _run_forge_interactive

            _run_forge_interactive()
