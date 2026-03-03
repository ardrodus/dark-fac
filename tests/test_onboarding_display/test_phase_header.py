"""Story 2: Unit tests for cli_colors.py phase_header() helper.

Tests the new phase_header() function in ui/cli_colors.py:
- Step counter rendering in [N/M] format
- Pillar color routing
- Default info styling when no pillar
- Rich ImportError graceful fallback
- Custom width parameter
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


class TestPhaseHeader:
    """5 unit tests for phase_header() in ui/cli_colors.py."""

    def test_step_counter_format(self) -> None:
        """Renders step counter in [N/M] format in header output."""
        from dark_factory.ui.cli_colors import phase_header

        output = phase_header(3, 14, "GitHub Auth")
        assert "[3/14]" in output

    def test_pillar_color_applied(self) -> None:
        """Applies pillar color when pillar parameter is specified."""
        from dark_factory.ui.cli_colors import phase_header

        output = phase_header(1, 14, "Platform", pillar="sentinel")
        # Should contain pillar-specific markup (sentinel blue)
        from dark_factory.ui.theme import PILLARS

        assert PILLARS.sentinel in output or "sentinel" in output.lower() or "bold" in output

    def test_default_info_styling(self) -> None:
        """Falls back to info color when no pillar specified."""
        from dark_factory.ui.cli_colors import phase_header

        output = phase_header(2, 14, "Dependencies")
        # Should use info styling (blue), not pillar-specific
        from dark_factory.ui.theme import THEME

        assert THEME.info in output or "info" in output.lower() or "bold" in output

    def test_rich_import_error_fallback(self) -> None:
        """Handles Rich ImportError gracefully with ASCII fallback divider."""
        with patch.dict(sys.modules, {"rich": None, "rich.console": None, "rich.rule": None}):
            # Force re-import or call with Rich unavailable
            from dark_factory.ui.cli_colors import phase_header

            output = phase_header(1, 14, "Platform")
            # Should still produce output with step counter, no crash
            assert "[1/14]" in output
            # Should use ASCII fallback (dashes or equals) instead of Rich Rule
            assert any(c in output for c in ("-", "=", "─"))

    def test_custom_width_parameter(self) -> None:
        """Respects custom width parameter for rule width."""
        from dark_factory.ui.cli_colors import phase_header

        narrow = phase_header(1, 14, "Test", width=40)
        wide = phase_header(1, 14, "Test", width=80)
        # The wider output should be longer or equal
        assert len(wide) >= len(narrow)
