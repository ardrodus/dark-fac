"""Theme definitions for the Dark Factory TUI.

Provides a centralised color palette and style constants that map to
Textual CSS variables.  The dashboard and any future TUI screens import
from here so visual consistency is maintained in one place.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PillarColors:
    """5-pillar color theme for the Dark Factory subsystems."""

    sentinel: str = "#3b82f6"     # Sentinel blue
    dark_forge: str = "#f97316"   # Dark Forge orange
    crucible: str = "#f59e0b"     # Crucible amber
    obelisk: str = "#10b981"      # Obelisk green
    ouroboros: str = "#7c3aed"    # Ouroboros purple


@dataclass(frozen=True, slots=True)
class ThemeColors:
    """Color palette for the Dark Factory TUI."""

    # 5-pillar subsystem colours
    pillar: PillarColors = PillarColors()

    # Primary brand (Ouroboros purple)
    primary: str = "#7c3aed"
    primary_light: str = "#a78bfa"

    # Semantic (CLI colour coding standard)
    success: str = "#22c55e"
    warning: str = "#f59e0b"   # amber
    error: str = "#ef4444"
    info: str = "#3b82f6"      # blue

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
PILLARS = THEME.pillar


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
    max-height: 16;
    border: tall {p_dark_forge};
    background: {bg_panel};
    padding: 1 2;
}}

#agent-panel {{
    height: auto;
    max-height: 14;
    border: tall {p_ouroboros};
    background: {bg_panel};
    padding: 1 2;
    width: 1fr;
}}

#health-panel {{
    height: auto;
    max-height: 10;
    border: tall {p_obelisk};
    background: {bg_panel};
    padding: 1 2;
    width: 1fr;
}}

#gate-panel {{
    height: auto;
    max-height: 12;
    border: tall {p_sentinel};
    background: {bg_panel};
    padding: 1 2;
}}

#security-panel {{
    height: auto;
    max-height: 14;
    border: tall {p_sentinel};
    background: {bg_panel};
    padding: 1 2;
}}

#notification-panel {{
    height: auto;
    max-height: 8;
    border: tall {p_crucible};
    background: {bg_panel};
    padding: 1 2;
}}

#log-panel {{
    height: 1fr;
    border: tall {border};
    background: {bg_panel};
    padding: 1 2;
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
        p_sentinel=PILLARS.sentinel,
        p_dark_forge=PILLARS.dark_forge,
        p_crucible=PILLARS.crucible,
        p_obelisk=PILLARS.obelisk,
        p_ouroboros=PILLARS.ouroboros,
    )


# ── Pipeline stage transition icons ──────────────────────────────

STAGE_ICONS: dict[str, str] = {
    "pending": "\u2504",   # ┄  dashed line
    "running": "\u25b6",   # ▶  play arrow
    "passed": "\u2714",    # ✔  checkmark
    "failed": "\u2718",    # ✘  X mark
    "skipped": "\u2500",   # ─  dash
}

TRANSITION_ARROW = " \u2192 "  # →


def stage_icon(state: str) -> str:
    """Return the visual icon for a pipeline stage state."""
    return STAGE_ICONS.get(state, "?")


# ── Relative timestamp formatting ────────────────────────────────


def format_relative_time(seconds_ago: float) -> str:
    """Format a duration in seconds as a human-friendly relative string."""
    if seconds_ago < 5:
        return "just now"
    if seconds_ago < 60:
        return f"{int(seconds_ago)}s ago"
    minutes = seconds_ago / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    return f"{int(days)}d ago"
