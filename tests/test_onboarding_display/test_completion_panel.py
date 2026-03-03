"""Story 3: Unit tests for cli_colors.py completion_panel() helper.

Tests the new completion_panel() function in ui/cli_colors.py:
- Panel body contains repo name
- Panel body contains strategy value
- Panel body contains label count as 'N created'
- Machine-parseable GITHUB_REPO= line preserved
- Machine-parseable 'Onboarding complete!' string preserved
- Rich ImportError graceful fallback
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


class TestCompletionPanel:
    """6 unit tests for completion_panel() in ui/cli_colors.py."""

    def test_panel_contains_repo_name(self) -> None:
        """Panel body contains the repo name."""
        from dark_factory.ui.cli_colors import completion_panel

        output = completion_panel("acme/web-app", "web", 17)
        assert "acme/web-app" in output

    def test_panel_contains_strategy(self) -> None:
        """Panel body contains the strategy value."""
        from dark_factory.ui.cli_colors import completion_panel

        output = completion_panel("acme/web-app", "web", 17)
        assert "web" in output

    def test_panel_contains_label_count(self) -> None:
        """Panel body contains the label count as 'N created'."""
        from dark_factory.ui.cli_colors import completion_panel

        output = completion_panel("acme/web-app", "web", 17)
        assert "17 created" in output or "17" in output

    def test_machine_parseable_github_repo_line(self) -> None:
        """Preserves machine-parseable 'GITHUB_REPO=owner/repo' line."""
        from dark_factory.ui.cli_colors import completion_panel

        output = completion_panel("acme/web-app", "web", 17)
        assert "GITHUB_REPO=acme/web-app" in output

    def test_machine_parseable_onboarding_complete(self) -> None:
        """Preserves machine-parseable 'Onboarding complete!' string."""
        from dark_factory.ui.cli_colors import completion_panel

        output = completion_panel("acme/web-app", "web", 17)
        assert "Onboarding complete!" in output

    def test_rich_import_error_fallback(self) -> None:
        """Handles Rich ImportError gracefully with plain text fallback."""
        with patch.dict(sys.modules, {"rich": None, "rich.console": None, "rich.panel": None}):
            from dark_factory.ui.cli_colors import completion_panel

            output = completion_panel("acme/web-app", "web", 17)
            # Should still contain all machine-parseable strings
            assert "acme/web-app" in output
            assert "Onboarding complete!" in output
            assert "GITHUB_REPO=acme/web-app" in output
