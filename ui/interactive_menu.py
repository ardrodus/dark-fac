"""Interactive terminal menu for Dark Factory.

Provides a single-character command loop for interactive operation:
``[d]ispatch``, ``[s]tatus``, ``[o]belisk``, ``[q]uit``, and ``[h]elp``.
Each command delegates to the appropriate extracted module.

This module is registered as *deferred* — it is only imported when the
user enters interactive mode.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from factory import __version__

if TYPE_CHECKING:
    from collections.abc import Callable


# ── Menu command dataclass ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MenuCommand:
    """A single interactive-menu command.

    Attributes
    ----------
    key:
        Single-character key that triggers this command.
    label:
        Human-readable label shown in the menu.
    description:
        One-line description of what the command does.
    handler:
        Callable invoked when the user presses this key.
    """

    key: str
    label: str
    description: str
    handler: Callable[[], None]


# ── Menu banner / rendering ────────────────────────────────────────

_BANNER = f"""\
\033[35m╔══════════════════════════════════════════╗\033[0m
\033[35m║\033[0m  \033[1;35m\u2593\u2593\033[0m \033[1mDark Factory\033[0m  v{__version__:<17s}\033[35m║\033[0m
\033[35m║\033[0m     Automated Issue-Dispatch Pipeline    \033[35m║\033[0m
\033[35m║\033[0m                                          \033[35m║\033[0m
\033[35m║\033[0m  \033[34m\u25cf\033[0m Sentinel  \033[33m\u25cf\033[0m Forge  \033[33m\u25cf\033[0m Crucible       \033[35m║\033[0m
\033[35m║\033[0m  \033[32m\u25cf\033[0m Obelisk   \033[35m\u25cf\033[0m Ouroboros              \033[35m║\033[0m
\033[35m╚══════════════════════════════════════════╝\033[0m"""


def render_banner() -> str:
    """Return the menu banner string."""
    return _BANNER


def render_menu(commands: tuple[MenuCommand, ...]) -> str:
    """Format the menu command list for display.

    Parameters
    ----------
    commands:
        Ordered tuple of available menu commands.

    Returns
    -------
    str
        Formatted menu text ready for printing.
    """
    lines: list[str] = ["", "\033[1mCommands:\033[0m"]
    for cmd in commands:
        key_hint = f"\033[1;36m[{cmd.key}]\033[0m"
        lines.append(f"  {key_hint} {cmd.label:<14s}  {cmd.description}")
    lines.append("")
    return "\n".join(lines)


def render_prompt() -> str:
    """Return the input prompt string."""
    return "\033[1;35mdark-factory\033[0m> "


# ── Command handlers (delegates to extracted modules) ──────────────


def _handle_dispatch() -> None:
    """Delegate to the issue dispatcher module."""
    from factory.dispatch.issue_dispatcher import (
        DispatcherState,
        auto_main_loop,
    )

    sys.stdout.write("Starting dispatch loop (press Ctrl+C to stop)...\n")
    state = DispatcherState()
    try:
        auto_main_loop(state=state, max_iterations=1)
    except KeyboardInterrupt:
        sys.stdout.write("\nDispatch interrupted.\n")
    sys.stdout.write("Dispatch cycle complete.\n")


def _handle_status() -> None:
    """Delegate to the status reporter module."""
    from factory.ui.status_reporter import show_status

    sys.stdout.write(show_status() + "\n")


def _handle_status_epics() -> None:
    """Show epic-level status via GitHub Milestones."""
    from factory.pipeline.epic_milestones import epic_status_summary, format_epic_summary

    repo = os.environ.get("DARK_FACTORY_REPO", os.environ.get("REPO", ""))
    if repo:
        statuses = epic_status_summary(repo)
        sys.stdout.write(format_epic_summary(statuses) + "\n")
    else:
        from factory.ui.status_reporter import show_epic_status

        sys.stdout.write(show_epic_status() + "\n")


def _handle_status_bootstrap() -> None:
    """Show bootstrap pipeline status."""
    from factory.ui.status_reporter import show_bootstrap_status

    sys.stdout.write(show_bootstrap_status() + "\n")


# ── Default command table ──────────────────────────────────────────


def _noop_help() -> None:
    """Placeholder — replaced at runtime by the menu loop."""


def build_default_commands() -> tuple[MenuCommand, ...]:
    """Build the default set of interactive menu commands.

    Returns
    -------
    tuple[MenuCommand, ...]
        Ordered tuple of the standard menu commands.
    """
    return (
        MenuCommand(key="d", label="Dispatch", description="Run one dispatch cycle", handler=_handle_dispatch),
        MenuCommand(key="s", label="Status", description="Show pipeline status", handler=_handle_status),
        MenuCommand(key="e", label="Epics", description="Show epic progress", handler=_handle_status_epics),
        MenuCommand(key="b", label="Bootstrap", description="Show bootstrap status", handler=_handle_status_bootstrap),
        MenuCommand(key="h", label="Help", description="Show this menu", handler=_noop_help),
        MenuCommand(key="q", label="Quit", description="Exit interactive mode", handler=_noop_help),
    )


# ── Command lookup ─────────────────────────────────────────────────


def build_command_map(commands: tuple[MenuCommand, ...]) -> dict[str, MenuCommand]:
    """Create a key → ``MenuCommand`` lookup from the command tuple.

    Parameters
    ----------
    commands:
        Ordered tuple of available menu commands.

    Returns
    -------
    dict[str, MenuCommand]
        Mapping from single-character key to its ``MenuCommand``.
    """
    return {cmd.key: cmd for cmd in commands}


# ── Main menu loop ─────────────────────────────────────────────────


def menu_loop(
    *,
    commands: tuple[MenuCommand, ...] | None = None,
    input_fn: Callable[[str], str] | None = None,
    output_fn: Callable[[str], None] | None = None,
    max_iterations: int | None = None,
) -> int:
    """Run the interactive menu loop.

    Parameters
    ----------
    commands:
        Menu commands to offer.  Defaults to :func:`build_default_commands`.
    input_fn:
        Callable for reading user input (defaults to :func:`input`).
        Signature: ``input_fn(prompt) -> str``.
    output_fn:
        Callable for writing output (defaults to ``sys.stdout.write``).
        Signature: ``output_fn(text) -> None``.
    max_iterations:
        Stop after this many iterations (``None`` = run until quit).
        Useful for testing.

    Returns
    -------
    int
        Exit code (0 = normal quit, 1 = error).
    """
    cmds = commands or build_default_commands()
    _input = input_fn or input
    _output = output_fn or sys.stdout.write
    cmd_map = build_command_map(cmds)
    menu_text = render_menu(cmds)

    _output(render_banner() + "\n")
    _output(menu_text)

    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        try:
            raw = _input(render_prompt())
        except (EOFError, KeyboardInterrupt):
            _output("\nGoodbye.\n")
            return 0

        key = raw.strip().lower()
        if not key:
            continue

        if key == "q":
            _output("Goodbye.\n")
            return 0

        if key == "h":
            _output(menu_text)
            continue

        matched = cmd_map.get(key)
        if matched is None:
            _output(f"\033[33mUnknown command: '{key}'\033[0m — press \033[1;36m[h]\033[0m for help.\n")
            continue

        try:
            matched.handler()
        except KeyboardInterrupt:
            _output("\n\033[33mInterrupted.\033[0m\n")
        except Exception as exc:  # noqa: BLE001
            _output(f"\033[31mError:\033[0m {exc}\n")
            _output("\033[90m  Hint: try running again or use [h] for help.\033[0m\n")

    return 0


# ── Entry helper ──────────────────────────────────────────────────


def run_interactive() -> None:
    """Launch the interactive menu and exit with its return code.

    Intended as a top-level entry point, e.g. from the CLI's
    ``dark-factory interactive`` command.
    """
    raise SystemExit(menu_loop())
