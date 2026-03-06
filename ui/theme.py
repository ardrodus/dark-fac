"""Theme definitions for the Dark Factory TUI.

Provides a centralised color palette and style constants that map to
Textual CSS variables.  The dashboard and any future TUI screens import
from here so visual consistency is maintained in one place.

Also defines the header banner — block-letter ASCII art with a racing
aesthetic and castle/factory silhouette.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    # Active / focused panel background (3-tier system)
    bg_active: str = "#283548"


# Singleton instance used throughout the UI layer.
THEME = ThemeColors()
PILLARS = THEME.pillar


# ── Subsystem theme definitions ──────────────────────────────────


@dataclass(frozen=True, slots=True)
class SubsystemTheme:
    """Color definition for a navigable subsystem.

    Used for dynamic CSS class swapping — each subsystem gets its own
    accent color applied to header backgrounds, panel borders, and
    focused-widget accents.
    """

    name: str
    accent: str
    css_class: str
    header_text: str = "#e2e8f0"


SUBSYSTEM_THEMES: dict[str, SubsystemTheme] = {
    "default": SubsystemTheme(
        name="Default", accent="#e2e8f0", css_class="theme-default",
        header_text="#0f172a",
    ),
    "sentinel": SubsystemTheme(
        name="Sentinel", accent="#3b82f6", css_class="theme-sentinel",
    ),
    "dark_forge": SubsystemTheme(
        name="Dark Forge", accent="#f97316", css_class="theme-dark-forge",
    ),
    "crucible": SubsystemTheme(
        name="Crucible", accent="#f59e0b", css_class="theme-crucible",
        header_text="#0f172a",
    ),
    "ouroboros": SubsystemTheme(
        name="Ouroboros", accent="#7c3aed", css_class="theme-ouroboros",
    ),
    "foundry": SubsystemTheme(
        name="Foundry", accent="#a78bfa", css_class="theme-foundry",
    ),
    "obelisk": SubsystemTheme(
        name="Obelisk", accent="#10b981", css_class="theme-obelisk",
    ),
    "settings": SubsystemTheme(
        name="Settings", accent="#94a3b8", css_class="theme-settings",
    ),
}

ALL_THEME_CLASSES: frozenset[str] = frozenset(
    t.css_class for t in SUBSYSTEM_THEMES.values()
)


# ── Header banner ─────────────────────────────────────────────────

# Rich-markup constant: block-letter ASCII art with DARK in orange
# (#f97316) and FACTORY in amber (#d97706).  Tight line spacing
# (no blank lines between rows) gives a forward-lean italic feel.

_DARK = "#f97316"
_FACT = "#d97706"

HEADER_BANNER: str = (
    f"[{_DARK}]██████╗  █████╗ ██████╗ ██╗  ██╗[/]"
    f"    [{_FACT}]███████╗ █████╗  ██████╗████████╗ ██████╗ ██████╗ ██╗   ██╗[/]\n"
    f"[{_DARK}]██╔══██╗██╔══██╗██╔══██╗██║ ██╔╝[/]"
    f"    [{_FACT}]██╔════╝██╔══██╗██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗╚██╗ ██╔╝[/]\n"
    f"[{_DARK}]██║  ██║███████║██████╔╝█████╔╝ [/]"
    f"    [{_FACT}]█████╗  ███████║██║        ██║   ██║   ██║██████╔╝ ╚████╔╝ [/]\n"
    f"[{_DARK}]██║  ██║██╔══██║██╔══██╗██╔═██╗ [/]"
    f"    [{_FACT}]██╔══╝  ██╔══██║██║        ██║   ██║   ██║██╔══██╗  ╚██╔╝  [/]\n"
    f"[{_DARK}]██████╔╝██║  ██║██║  ██║██║  ██╗[/]"
    f"    [{_FACT}]██║     ██║  ██║╚██████╗   ██║   ╚██████╔╝██║  ██║   ██║   [/]\n"
    f"[{_DARK}]╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝[/]"
    f"    [{_FACT}]╚═╝     ╚═╝  ╚═╝ ╚═════╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝   ╚═╝   [/]"
)

# Castle/factory silhouette — turrets with smokestacks and a gated
# entrance.  Rendered in muted border tones so it doesn't overpower
# the block-letter text.

_B = THEME.border          # #334155  — structure lines
_M = THEME.text_muted      # #94a3b8  — fills / details

FACTORY_SILHOUETTE: str = (
    f"[{_M}]            ·    ·                 ·    ·[/]\n"
    f"[{_B}]       ┌────┤    ├─────────────────┤    ├────┐[/]\n"
    f"[{_B}]       │[/][{_M}]░░░░[/][{_B}]│    │[/]"
    f"[{_M}]░░░░░░░░░░░░░░░░░[/][{_B}]│    │[/][{_M}]░░░░[/][{_B}]│[/]\n"
    f"[{_B}]       │    └────┘                 └────┘    │[/]\n"
    f"[{_B}]       │         [/]"
    f"[{_M}]▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓[/]"
    f"[{_B}]          │[/]\n"
    f"[{_B}]       └──────────────╤══════════╤───────────┘[/]\n"
    f"[{_B}]                      │[/]  [{_M}]▓▓▓▓▓▓[/]  [{_B}]│[/]\n"
    f"[{_B}]                      ╘══════════╛[/]"
)

# Compact single-line subsystem icon variants for inline labels.
# Full multi-line ASCII art icons are defined later alongside STAGE_ICONS.
COMPACT_ICONS: dict[str, str] = {
    "sentinel": "\u25a3",     # ▣  contained square — scanner grid
    "dark_forge": "\u2692",   # ⚒  hammer and pick — anvil / forge
    "crucible": "\u2697",     # ⚗  alembic — flask
    "foundry": "\u26bf",      # ⚿  squared key — key slot
    "obelisk": "\u26bf",      # ⚿  squared key (alias for foundry)
    "ouroboros": "\u221e",    # ∞  infinity — recursive loop
}

# Subtitle bar — subsystem icons for each of the five pillars.

SUBTITLE_BAR: str = (
    f"[{PILLARS.sentinel}]{COMPACT_ICONS['sentinel']}[/] Sentinel   "
    f"[{PILLARS.dark_forge}]{COMPACT_ICONS['dark_forge']}[/] Dark Forge   "
    f"[{PILLARS.crucible}]{COMPACT_ICONS['crucible']}[/] Crucible   "
    f"[{PILLARS.obelisk}]{COMPACT_ICONS['obelisk']}[/] Obelisk   "
    f"[{PILLARS.ouroboros}]{COMPACT_ICONS['ouroboros']}[/] Ouroboros"
)

# Full header: silhouette + block text + divider + subtitle bar.

FULL_HEADER_BANNER: str = (
    FACTORY_SILHOUETTE
    + "\n\n"
    + HEADER_BANNER
    + "\n"
    + f"[{_B}]{'─' * 97}[/]"
    + "\n"
    + SUBTITLE_BAR
)


# ── Textual CSS ───────────────────────────────────────────────────

# CSS string injected into every DashboardApp via ``DEFAULT_CSS``.
# Uses Textual CSS syntax — variables are **not** used because
# Textual doesn't support CSS custom properties; instead we inline
# the colour values from the theme.


def build_theme_css() -> str:
    """Generate Textual CSS rules for dynamic subsystem theme switching.

    Each subsystem theme is represented as a CSS class on the Screen widget.
    Rules target Header background, ``.themed-border`` borders, DataTable
    headers, and ListView focus-highlight accents.
    """
    rules: list[str] = []
    for theme in SUBSYSTEM_THEMES.values():
        a = theme.accent
        c = theme.css_class
        t = theme.header_text
        rules.append(
            f"Screen.{c} Header {{\n"
            f"    background: {a};\n"
            f"    color: {t};\n"
            f"}}\n"
            f"\n"
            f"Screen.{c} .themed-border {{\n"
            f"    border: round {a};\n"
            f"}}\n"
            f"\n"
            f"Screen.{c} DataTable > .datatable--header {{\n"
            f"    background: {a};\n"
            f"    color: {t};\n"
            f"}}\n"
            f"\n"
            f"Screen.{c} ListView:focus > .listview--highlight {{\n"
            f"    background: {a} 30%;\n"
            f"}}\n"
            f"\n"
            f"Screen.{c} ListView:focus > .listview--highlight-top {{\n"
            f"    background: {a} 30%;\n"
            f"}}"
        )
    return "\n\n".join(rules)


def build_css() -> str:
    """Return the Textual CSS for dynamic theme switching.

    Static CSS has been extracted to .tcss files under ``ui/styles/``.
    This function now returns only the dynamic subsystem theme rules.
    """
    return build_theme_css()


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


# ── Subsystem ASCII art icons ───────────────────────────────────
# Source design: dark-factory-icons.html
# Multi-line art for splash / header display; compact single-line
# variants for inline panel labels.

ICON_SENTINEL: str = (
    "\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557\n"  # ╔═══════════╗
    "\u2551 \u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591 \u2551\n"              # ║ ░░░░░░░░░ ║
    "\u2551 \u2591 SCAN  \u2591 \u2551\n"                                                 # ║ ░ SCAN  ░ ║
    "\u2551 \u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591 \u2551\n"              # ║ ░░░░░░░░░ ║
    "\u2551 \u2591 GATE  \u2591 \u2551\n"                                                 # ║ ░ GATE  ░ ║
    "\u2551 \u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591 \u2551\n"              # ║ ░░░░░░░░░ ║
    "\u2551 \u2591VERIFY \u2591 \u2551\n"                                                 # ║ ░VERIFY ░ ║
    "\u2551 \u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591 \u2551\n"              # ║ ░░░░░░░░░ ║
    "\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d"     # ╚═══════════╝
)

ICON_DARK_FORGE: str = (
    "    . * .\n"
    "  *  \\|/  .\n"
    "     \\|/\n"
    "\u2550\u2550\u2550\u2550\u2550\u2550\u2564\u2550\u2550\u2550\u2550\u2550\u2550\n"  # ══════╤══════
    "\u250c\u2500\u2500\u2500\u2500\u2500\u2534\u2500\u2500\u2500\u2500\u2500\u2510\n"  # ┌─────┴─────┐
    "\u2502           \u2502\n"                                                            # │           │
    "\u2502 \u2593\u2593\u2593\u2593\u2593\u2593\u2593\u2593\u2593 \u2502\n"              # │ ▓▓▓▓▓▓▓▓▓ │
    "\u2502 \u2593\u2593\u2593\u2593\u2593\u2593\u2593\u2593\u2593 \u2502\n"              # │ ▓▓▓▓▓▓▓▓▓ │
    "\u2514\u2500\u2510       \u250c\u2500\u2518\n"                                       # └─┐       ┌─┘
    "  \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518"                            #   └───────┘
)

ICON_CRUCIBLE: str = (
    "   \u250c\u2500\u2500\u2510\n"                                                       #    ┌──┐
    "   \u2502  \u2502\n"                                                                  #    │  │
    "\u250c\u2500\u2500\u2518  \u2514\u2500\u2500\u2510\n"                                # ┌──┘  └──┐
    "\u2502 \u2591\u2592\u2593\u2593\u2592\u2591 \u2502\n"                                # │ ░▒▓▓▒░ │
    "\u2502 \u2592\u2593\u2588\u2588\u2593\u2592 \u2502\n"                                # │ ▒▓██▓▒ │
    "\u2502 \u2591\u2592\u2593\u2593\u2592\u2591 \u2502\n"                                # │ ░▒▓▓▒░ │
    "\u2514\u2500\u2500\u2510  \u250c\u2500\u2500\u2518\n"                                # └──┐  ┌──┘
    "   \u2514\u2500\u2500\u2518"                                                          #    └──┘
)

ICON_FOUNDRY: str = (
    "  .  .  .  .  .\n"
    "   . . . . .\n"
    "       \u250c\u2510\n"                                                                #        ┌┐
    "  )   \u250c\u2518\u2514\u2510   (\n"                                                 #   )   ┌┘└┐   (
    " ))  \u250c\u2518**\u2514\u2510  ((\n"                                                # ))  ┌┘**└┐  ((
    "))) \u250c\u2518    \u2514\u2510 (((\n"                                               # ))) ┌┘    └┐ (((
    " )) \u2502  *   \u2502 ((\n"                                                          #  )) │  *   │ ((
    "  ) \u2502  |   \u2502 (\n"                                                           #   ) │  |   │ (
    "    \u2502  |   \u2502\n"                                                              #     │  |   │
    "    \u2502  *   \u2502\n"                                                              #     │  *   │
    "    \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2518"                                 #     └──────┘
)

ICON_OUROBOROS: str = (
    "\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557\n"  # ╔═════════════╗
    "\u2551 \u256d\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256e \u2551\n"              # ║ ╭─────────╮ ║
    "\u2551 \u2502 \u256d\u2500\u2500\u2500\u2500\u2500\u256e \u2502 \u2551\n"                        # ║ │ ╭─────╮ │ ║
    "\u2551 \u2502 \u2502  *  \u2502 \u2502 \u2551\n"                                                 # ║ │ │  *  │ │ ║
    "\u2551 \u2502 \u2570\u2500\u2500^\u2500\u2500\u256f \u2502 \u2551\n"                              # ║ │ ╰──^──╯ │ ║
    "\u2551 \u2570\u2500\u2500\u2500\u2500+\u2500\u2500\u2500\u2500\u256f \u2551\n"                    # ║ ╰────+────╯ ║
    "\u255a\u2550\u2550\u2550\u2550\u2550\u2550>\u2550\u2550\u2550\u2550\u2550\u2550\u255d"           # ╚══════>══════╝
)

# Convenience lookup: subsystem key → multi-line icon art.
SUBSYSTEM_ICONS: dict[str, str] = {
    "sentinel": ICON_SENTINEL,
    "dark_forge": ICON_DARK_FORGE,
    "crucible": ICON_CRUCIBLE,
    "foundry": ICON_FOUNDRY,
    "obelisk": ICON_FOUNDRY,
    "ouroboros": ICON_OUROBOROS,
}

def subsystem_icon(name: str, *, compact: bool = False) -> str:
    """Return the icon for a subsystem pillar.

    Parameters
    ----------
    name:
        Subsystem key (e.g. ``"sentinel"``, ``"dark_forge"``).
    compact:
        If ``True``, return the single-character inline variant.

    Returns
    -------
    str
        Multi-line ASCII art or a single-character glyph.
    """
    if compact:
        return COMPACT_ICONS.get(name, "\u25a0")
    return SUBSYSTEM_ICONS.get(name, "")


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


# ── Dynamic theme switching ──────────────────────────────────────


def apply_subsystem_theme(app: Any, subsystem: str) -> None:
    """Apply a subsystem color theme via CSS class swap on the Screen.

    Removes all existing theme classes, then adds the one matching
    *subsystem*.  Pass ``"default"`` (or use :func:`reset_theme`) to
    restore the neutral main-menu theme.
    """
    screen = app.screen
    for cls in ALL_THEME_CLASSES:
        screen.remove_class(cls)
    theme = SUBSYSTEM_THEMES.get(subsystem)
    if theme:
        screen.add_class(theme.css_class)


def reset_theme(app: Any) -> None:
    """Reset to the default neutral theme."""
    apply_subsystem_theme(app, "default")
