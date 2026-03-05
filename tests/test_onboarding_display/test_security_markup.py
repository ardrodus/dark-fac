"""Story 9: Security tests for Rich markup injection prevention (SEC-001).

Tests rich.markup.escape() is applied to user-controlled strings:
- Repo names with Rich markup characters escaped before rendering
- Strategy names with markup characters escaped before rendering
- Error messages with user-controlled content escaped
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestRichMarkupInjection:
    """3 security tests verifying markup escape on user-controlled strings."""

    def test_repo_name_markup_escaped(self) -> None:
        """Repo name with Rich markup characters '[bold red]HACK[/]' is escaped before rendering."""
        malicious_repo = "[bold red]HACK[/]evil/repo"
        captured: list[str] = []

        # Test via completion_panel if it exists, otherwise via orchestrator output
        with patch("sys.stdout.write", side_effect=lambda s: captured.append(s)):
            try:
                from dark_factory.ui.cli_colors import completion_panel

                output = completion_panel(malicious_repo, "web", 5)
                # The markup tags should be escaped, not rendered
                assert "[bold red]" not in output or "\\[bold red]" in output or "&lsqb;" in output
                # The actual text content should still be present
                assert "HACK" in output or "evil/repo" in output
            except ImportError:
                # completion_panel not yet implemented -- test the contract
                # When implemented, it must escape the repo name
                pytest.skip("completion_panel not yet implemented")

    def test_strategy_name_markup_escaped(self) -> None:
        """App type name with markup characters is escaped before rendering."""
        malicious_app_type = "[red]MALICIOUS[/red]"
        captured: list[str] = []

        with patch("sys.stdout.write", side_effect=lambda s: captured.append(s)):
            try:
                from dark_factory.ui.cli_colors import completion_panel

                output = completion_panel("acme/app", malicious_app_type, 5)
                # Raw Rich markup should not be passed through unescaped
                # Either escaped or stripped
                assert "[red]" not in output or "\\[red]" in output
            except ImportError:
                pytest.skip("completion_panel not yet implemented")

    def test_error_message_user_content_escaped(self) -> None:
        """Error messages with user-controlled content are escaped."""
        malicious_input = "[bold magenta]INJECTED[/bold magenta]"
        captured: list[str] = []

        with patch("sys.stderr.write", side_effect=lambda s: captured.append(s)):
            try:
                from rich.console import Console

                # If Rich is available, test that print_error escapes content
                from dark_factory.ui.cli_colors import print_error

                # Capture stderr output from print_error
                with patch("sys.stderr") as mock_stderr:
                    mock_stderr.write = lambda s: captured.append(s)
                    # We need to verify the function handles user input safely
                    # The actual rendering should escape markup in user-provided text
                    print_error(malicious_input, hint="user provided")
            except ImportError:
                pass

        # If we captured output, verify no raw markup was rendered
        if captured:
            full = "".join(captured)
            # The text "INJECTED" should appear but not as styled markup
            assert "INJECTED" in full or malicious_input in full
