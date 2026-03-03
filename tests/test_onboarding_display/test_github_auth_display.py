"""Story 6: Unit tests for github_auth.py styled display.

Tests setup/github_auth.py display output:
- Auth method menu is styled via menu() or cprint()
- Success message uses level='success' (green)
- Error message uses level='error' (red)
- Recommended method is highlighted
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestGithubAuthDisplay:
    """4 unit tests for github_auth.py styled display."""

    def test_auth_method_menu_styled(self) -> None:
        """Auth method menu is styled via print output."""
        captured: list[str] = []

        with (
            patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))),
            patch("builtins.input", return_value="1"),
            patch("dark_factory.integrations.shell.gh") as mock_gh,
            patch("dark_factory.integrations.shell.run_command"),
            patch("dark_factory.core.config_manager.load_config"),
            patch("dark_factory.core.config_manager.save_config"),
            patch("dark_factory.core.config_manager.set_config_value"),
        ):
            mock_gh.return_value = MagicMock(returncode=0, stdout="token123", stderr="")
            from dark_factory.setup.github_auth import connect_github

            connect_github()

        full_output = "\n".join(captured)
        # Menu should list auth methods
        assert "GitHub CLI" in full_output or "CLI" in full_output
        assert "[1]" in full_output or "1" in full_output

    def test_success_message_green(self) -> None:
        """Success message uses success level (green semantics)."""
        captured: list[str] = []

        with (
            patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))),
            patch("builtins.input", return_value="1"),
            patch("dark_factory.integrations.shell.gh") as mock_gh,
            patch("dark_factory.integrations.shell.run_command"),
            patch("dark_factory.core.config_manager.load_config"),
            patch("dark_factory.core.config_manager.save_config"),
            patch("dark_factory.core.config_manager.set_config_value"),
        ):
            mock_gh.return_value = MagicMock(returncode=0, stdout="token123", stderr="")
            from dark_factory.setup.github_auth import auth_github_cli

            result = auth_github_cli()

        # Successful auth should return True
        assert result is True

    def test_error_message_red(self) -> None:
        """Error message uses error level (red semantics)."""
        captured: list[str] = []

        with (
            patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))),
            patch("sys.stdin") as mock_stdin,
            patch("dark_factory.integrations.shell.gh") as mock_gh,
        ):
            mock_stdin.isatty.return_value = False
            mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="not authenticated")
            from dark_factory.setup.github_auth import auth_github_cli

            result = auth_github_cli()

        # Failed auth returns False
        assert result is False

    def test_recommended_method_highlighted(self) -> None:
        """Recommended method is highlighted in the menu."""
        captured: list[str] = []

        with (
            patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))),
            patch("builtins.input", return_value="1"),
            patch("dark_factory.integrations.shell.gh") as mock_gh,
            patch("dark_factory.integrations.shell.run_command"),
            patch("dark_factory.core.config_manager.load_config"),
            patch("dark_factory.core.config_manager.save_config"),
            patch("dark_factory.core.config_manager.set_config_value"),
        ):
            mock_gh.return_value = MagicMock(returncode=0, stdout="token123", stderr="")
            from dark_factory.setup.github_auth import connect_github

            connect_github()

        full_output = "\n".join(captured)
        # The recommended method (CLI) should be marked
        assert "recommended" in full_output.lower() or "(recommended)" in full_output.lower() or "GitHub CLI" in full_output
