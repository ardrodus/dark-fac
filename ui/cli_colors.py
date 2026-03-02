"""Consistent CLI colour output helpers for the Dark Factory.

Standard colour coding:
  - success = green
  - warning = amber
  - error   = red
  - info    = blue

Uses Rich markup for coloured terminal output.  All public functions
accept plain strings and return Rich-markup-annotated strings, or
print directly via :func:`cprint`.
"""

from __future__ import annotations

import sys
from typing import TextIO

from factory.ui.theme import PILLARS, THEME, stage_icon

# ── Semantic colour map ──────────────────────────────────────────

_LEVEL_STYLE: dict[str, str] = {
    "success": f"bold {THEME.success}",
    "warning": f"bold {THEME.warning}",
    "error": f"bold {THEME.error}",
    "info": f"bold {THEME.info}",
    "muted": THEME.text_muted,
}

# Pillar styles for subsystem-specific output
_PILLAR_STYLE: dict[str, str] = {
    "sentinel": f"bold {PILLARS.sentinel}",
    "dark_forge": f"bold {PILLARS.dark_forge}",
    "crucible": f"bold {PILLARS.crucible}",
    "obelisk": f"bold {PILLARS.obelisk}",
    "ouroboros": f"bold {PILLARS.ouroboros}",
}


def styled(text: str, level: str = "info") -> str:
    """Wrap *text* in Rich markup for the given semantic level."""
    style = _LEVEL_STYLE.get(level, "")
    return f"[{style}]{text}[/]" if style else text


def pillar_styled(text: str, pillar: str) -> str:
    """Wrap *text* in Rich markup for a pillar subsystem colour."""
    style = _PILLAR_STYLE.get(pillar, "")
    return f"[{style}]{text}[/]" if style else text


# ── Stage verdict formatting ─────────────────────────────────────

_VERDICT_LEVEL: dict[str, str] = {
    "PASS": "success",
    "passed": "success",
    "FAIL": "error",
    "failed": "error",
    "WARN": "warning",
    "warning": "warning",
    "skipped": "muted",
    "pending": "muted",
    "running": "info",
}


def verdict_tag(state: str) -> str:
    """Format a pipeline stage state as ``[icon STATE]`` with colour."""
    icon = stage_icon(state)
    level = _VERDICT_LEVEL.get(state, "info")
    label = state.upper()
    return styled(f"{icon} {label}", level)


# ── Convenience printers ─────────────────────────────────────────


def cprint(
    text: str,
    level: str = "info",
    *,
    file: TextIO | None = None,
    end: str = "\n",
) -> None:
    """Print *text* with Rich markup to *file* (default: stdout).

    Falls back to plain text if Rich console is unavailable.
    """
    stream = file or sys.stdout
    try:
        from rich.console import Console

        console = Console(file=stream, highlight=False)
        console.print(styled(text, level), end=end)
    except ImportError:
        stream.write(f"{text}{end}")


def print_stage_result(name: str, state: str, detail: str = "") -> None:
    """Print a single pipeline stage result line with visual feedback."""
    tag = verdict_tag(state)
    suffix = f"  {detail}" if detail else ""
    try:
        from rich.console import Console

        console = Console(highlight=False)
        console.print(f"  {tag}  {name}{suffix}")
    except ImportError:
        icon = stage_icon(state)
        sys.stdout.write(f"  {icon} {state.upper():>7}  {name}{suffix}\n")


def print_error(message: str, *, hint: str = "") -> None:
    """Print a human-friendly error message with optional next-step hint."""
    cprint(f"Error: {message}", "error", file=sys.stderr)
    if hint:
        cprint(f"  Hint: {hint}", "muted", file=sys.stderr)
