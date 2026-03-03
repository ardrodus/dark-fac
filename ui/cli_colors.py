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

from dark_factory.ui.theme import PILLARS, THEME, stage_icon

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


def phase_header(
    step: int,
    total: int,
    title: str,
    *,
    pillar: str = "",
    width: int = 60,
) -> str:
    """Return a styled phase header with ``[step/total] title`` and a divider.

    Uses :func:`pillar_styled` when *pillar* is non-empty, otherwise
    :func:`styled` with ``level='info'``.  Falls back to plain ASCII
    when Rich is unavailable.
    """
    counter = f"[{step}/{total}]"
    label = f"{counter} {title}"

    try:
        from rich.markup import escape  # noqa: PLC0415

        safe_counter = escape(counter)
        safe_label = f"{safe_counter} {title}"

        if pillar:
            header_line = pillar_styled(safe_label, pillar)
        else:
            header_line = styled(safe_label, "info")

        rule = f"[dim]{'─' * width}[/]"
        return f"{header_line}\n{rule}\n"
    except ImportError:
        divider = "─" * width
        return f"{label}\n{divider}\n"


def completion_panel(repo: str, strategy: str, label_count: int) -> str:
    """Return a styled onboarding completion summary.

    Preserves machine-parseable ``Onboarding complete!`` and
    ``GITHUB_REPO=owner/repo`` lines.  Escapes *repo* and *strategy*
    with :func:`rich.markup.escape` when Rich is available (SEC-001).
    """
    try:
        from rich.markup import escape  # noqa: PLC0415

        safe_repo = escape(repo)
        safe_strategy = escape(strategy)

        body = (
            f"Onboarding complete!\n"
            f"\n"
            f"  Repository: {safe_repo}\n"
            f"  Strategy:   {safe_strategy}\n"
            f"  Labels:     {label_count} created\n"
            f"  GITHUB_REPO={safe_repo}"
        )

        border = f"[green]{'─' * 40}[/]"
        header = "[bold green]  Onboarding Summary[/]"
        return f"{border}\n{header}\n{border}\n{body}\n{border}\n"
    except ImportError:
        sep = "─" * 30
        return (
            f"Onboarding complete!\n"
            f"\n"
            f"  Onboarding Summary\n"
            f"  {sep}\n"
            f"  Repository: {repo}\n"
            f"  Strategy:   {strategy}\n"
            f"  Labels:     {label_count} created\n"
            f"  GITHUB_REPO={repo}\n"
            f"  {sep}\n"
        )


def print_error(message: str, *, hint: str = "") -> None:
    """Print a human-friendly error message with optional next-step hint."""
    cprint(f"Error: {message}", "error", file=sys.stderr)
    if hint:
        cprint(f"  Hint: {hint}", "muted", file=sys.stderr)
