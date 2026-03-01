"""Theme definitions for the Dark Factory TUI.

Provides a centralised color palette and style constants that map to
Textual CSS variables.  The dashboard and any future TUI screens import
from here so visual consistency is maintained in one place.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemeColors:
    """Color palette for the Dark Factory TUI."""

    # Primary brand
    primary: str = "#7c3aed"
    primary_light: str = "#a78bfa"

    # Semantic
    success: str = "#22c55e"
    warning: str = "#eab308"
    error: str = "#ef4444"
    info: str = "#3b82f6"

    # Surface / background
    bg_dark: str = "#0f172a"
    bg_panel: str = "#1e293b"
    bg_header: str = "#7c3aed"

    # Text
    text: str = "#e2e8f0"
    text_muted: str = "#94a3b8"
    text_accent: str = "#a78bfa"

    # Borders
    border: str = "#334155"
    border_focus: str = "#7c3aed"


# Singleton instance used throughout the UI layer.
THEME = ThemeColors()


# ── Textual CSS ───────────────────────────────────────────────────

# CSS string injected into every DashboardApp via ``DEFAULT_CSS``.
# Uses Textual CSS syntax — variables are **not** used because
# Textual doesn't support CSS custom properties; instead we inline
# the colour values from the theme.

_CSS_TEMPLATE = """
Screen {{
    background: {bg_dark};
}}

Header {{
    background: {bg_header};
    color: {text};
}}

Footer {{
    background: {bg_panel};
    color: {text_muted};
}}

#pipeline-panel {{
    height: auto;
    max-height: 14;
    border: solid {border};
    background: {bg_panel};
    padding: 0 1;
}}

#agent-panel {{
    height: auto;
    max-height: 12;
    border: solid {border};
    background: {bg_panel};
    padding: 0 1;
}}

#health-panel {{
    height: auto;
    max-height: 8;
    border: solid {border};
    background: {bg_panel};
    padding: 0 1;
}}

#log-panel {{
    height: 1fr;
    border: solid {border};
    background: {bg_panel};
    padding: 0 1;
}}

DataTable {{
    background: {bg_panel};
    color: {text};
}}

DataTable > .datatable--header {{
    background: {primary};
    color: {text};
}}

ProgressBar Bar > .bar--bar {{
    color: {primary};
}}

ProgressBar Bar > .bar--complete {{
    color: {success};
}}

RichLog {{
    background: {bg_panel};
    color: {text};
    scrollbar-color: {text_muted};
}}

.success {{
    color: {success};
}}

.warning {{
    color: {warning};
}}

.error {{
    color: {error};
}}

.muted {{
    color: {text_muted};
}}

.accent {{
    color: {text_accent};
}}
"""


def build_css() -> str:
    """Return the Textual CSS string with theme colours interpolated."""
    return _CSS_TEMPLATE.format(
        bg_dark=THEME.bg_dark,
        bg_header=THEME.bg_header,
        bg_panel=THEME.bg_panel,
        border=THEME.border,
        primary=THEME.primary,
        success=THEME.success,
        warning=THEME.warning,
        error=THEME.error,
        text=THEME.text,
        text_muted=THEME.text_muted,
        text_accent=THEME.text_accent,
    )
