"""Tests for _run_forge_interactive in cli/dispatch.py.

Verifies that the interactive forge command runs ONLY the Dark Forge
pipeline and does NOT chain into Crucible, Deploy, or Ouroboros.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    """Verify _run_forge_interactive calls run_dark_forge, not run_cycle."""

    @patch("dark_factory.cli.dispatch.input", return_value="")
    @patch("dark_factory.cli.dispatch.sys")
    def test_calls_run_dark_forge_not_run_cycle(
        self, mock_sys: MagicMock, _mock_input: MagicMock,
    ) -> None:
        """Interactive forge must call run_dark_forge, never run_cycle."""
        mock_sys.stdout = MagicMock()

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
                "dark_factory.modes.auto.run_dark_forge",
                return_value=True,
            ) as mock_forge,
            patch(
                "dark_factory.modes.auto.run_cycle",
                side_effect=AssertionError("run_cycle must NOT be called"),
            ),
        ):
            from dark_factory.cli.dispatch import _run_forge_interactive

            _run_forge_interactive()

        mock_forge.assert_called_once()

    @patch("dark_factory.cli.dispatch.input", return_value="")
    @patch("dark_factory.cli.dispatch.sys")
    def test_forge_failure_reports_failed(
        self, mock_sys: MagicMock, _mock_input: MagicMock,
    ) -> None:
        """When run_dark_forge returns False, output should say FAILED."""
        buf: list[str] = []
        mock_sys.stdout = MagicMock()
        mock_sys.stdout.write = lambda s: buf.append(s)
        mock_sys.stdout.flush = lambda: None

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
                "dark_factory.modes.auto.run_dark_forge",
                return_value=False,
            ),
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
        """When run_dark_forge returns True, output should say PASSED."""
        buf: list[str] = []
        mock_sys.stdout = MagicMock()
        mock_sys.stdout.write = lambda s: buf.append(s)
        mock_sys.stdout.flush = lambda: None

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
                "dark_factory.modes.auto.run_dark_forge",
                return_value=True,
            ),
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
        """When no issues are queued, return without calling forge."""
        mock_sys.stdout = MagicMock()

        with (
            patch(
                "dark_factory.dispatch.issue_dispatcher.select_next_issue",
                return_value=None,
            ),
            patch(
                "dark_factory.modes.auto.run_dark_forge",
                side_effect=AssertionError("should not be called"),
            ),
        ):
            from dark_factory.cli.dispatch import _run_forge_interactive

            _run_forge_interactive()

    @patch("dark_factory.cli.dispatch.input", return_value="")
    @patch("dark_factory.cli.dispatch.sys")
    def test_workspace_error_returns_early(
        self, mock_sys: MagicMock, _mock_input: MagicMock,
    ) -> None:
        """When workspace acquisition fails, return without calling forge."""
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
            patch(
                "dark_factory.modes.auto.run_dark_forge",
                side_effect=AssertionError("should not be called"),
            ),
        ):
            from dark_factory.cli.dispatch import _run_forge_interactive

            _run_forge_interactive()
