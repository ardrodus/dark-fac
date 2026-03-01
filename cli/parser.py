"""CLI argument parser for dark-factory.

Parses raw CLI tokens into a typed ``ParsedCommand`` dataclass
with command routing, flag validation, and ``--help`` text generation.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from factory import __version__

if TYPE_CHECKING:
    from collections.abc import Callable


# ── Parsed result ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """Result of parsing CLI arguments.

    Attributes
    ----------
    command:
        The subcommand name (e.g. ``"doctor"``, ``"smoke-test"``).
    flags:
        Boolean flags parsed from the command line.
    args:
        Positional arguments after the subcommand.
    """

    command: str
    flags: dict[str, bool]
    args: tuple[str, ...]


# ── Command routing table ─────────────────────────────────────────

COMMAND_TABLE: dict[str, str] = {
    "doctor": "Run system health checks.",
    "gates": "Discover and run validation gates.",
    "selftest": "Run all built-in validators and report issues.",
    "smoke-test": "Run a trivial Python story through the pipeline end-to-end.",
    "status": "Show pipeline, epic, or bootstrap status.",
}


# ── Help text generation ──────────────────────────────────────────


def _format_help() -> str:
    """Generate top-level help text matching Click output format."""
    lines = [
        "Usage: dark-factory [OPTIONS] COMMAND [ARGS]...",
        "",
        "  Dark Factory \u2014 automated issue-dispatch pipeline.",
        "",
        "Options:",
        "  --version  Show the version and exit.",
        "  --help     Show this message and exit.",
        "",
        "Commands:",
    ]
    col_width = max(len(name) for name in COMMAND_TABLE)
    for name, desc in COMMAND_TABLE.items():
        padding = " " * (col_width - len(name) + 2)
        lines.append(f"  {name}{padding}{desc}")
    return "\n".join(lines) + "\n"


# ── Path resolution ───────────────────────────────────────────────


def resolve_home(path: str) -> str:
    """Expand ``~`` to the user's home directory.

    Parameters
    ----------
    path:
        A filesystem path, possibly starting with ``~``.

    Returns
    -------
    str
        The path with ``~`` expanded to an absolute path.
    """
    return str(Path(path).expanduser())


# ── Subcommand parsers ────────────────────────────────────────────


def _parse_doctor(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory doctor`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory doctor",
        description="Run system health checks.",
    )
    parser.add_argument(
        "--migration",
        action="store_true",
        help="Show bash\u2192Python migration progress.",
    )
    parser.add_argument(
        "--modules",
        action="store_true",
        help="Validate the module manifest.",
    )
    parser.add_argument(
        "--debug-modules",
        action="store_true",
        dest="debug_modules",
        help="Show which modules loaded and when.",
    )
    parser.add_argument(
        "--deps",
        action="store_true",
        help="Validate the module dependency graph.",
    )
    parser.add_argument(
        "--lint",
        action="store_true",
        help="Run file size compliance check.",
    )
    ns = parser.parse_args(argv)
    return ParsedCommand(
        command="doctor",
        flags={
            "migration": ns.migration,
            "modules": ns.modules,
            "debug_modules": ns.debug_modules,
            "deps": ns.deps,
            "lint": ns.lint,
        },
        args=(),
    )


def _parse_smoke_test(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory smoke-test`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory smoke-test",
        description="Run a trivial Python story through the pipeline end-to-end.",
    )
    parser.add_argument(
        "title",
        nargs="?",
        default="trivial-python-story",
        help="Story title for the smoke test.",
    )
    ns = parser.parse_args(argv)
    return ParsedCommand(
        command="smoke-test",
        flags={},
        args=(ns.title,),
    )


def _parse_status(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory status`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory status",
        description="Show pipeline, epic, or bootstrap status.",
    )
    parser.add_argument(
        "--epics",
        action="store_true",
        help="Show epic-level progress.",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Show bootstrap pipeline status.",
    )
    ns = parser.parse_args(argv)
    return ParsedCommand(
        command="status",
        flags={"epics": ns.epics, "bootstrap": ns.bootstrap},
        args=(),
    )


def _parse_gates(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory gates`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory gates",
        description="Discover and run validation gates.",
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        dest="run_all",
        help="Run every discovered gate in sequence.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_gates",
        help="List all registered gates with their check counts.",
    )
    parser.add_argument(
        "--run",
        default="",
        dest="run_name",
        help="Run a single gate by name.",
    )
    ns = parser.parse_args(argv)
    return ParsedCommand(
        command="gates",
        flags={
            "run_all": ns.run_all,
            "list_gates": ns.list_gates,
        },
        args=(ns.run_name,) if ns.run_name else (),
    )


def _parse_selftest(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory selftest`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory selftest",
        description="Run all built-in validators and report issues.",
    )
    parser.parse_args(argv)
    return ParsedCommand(command="selftest", flags={}, args=())


_SUBCOMMAND_PARSERS: dict[str, Callable[[list[str]], ParsedCommand]] = {
    "doctor": _parse_doctor,
    "gates": _parse_gates,
    "selftest": _parse_selftest,
    "smoke-test": _parse_smoke_test,
    "status": _parse_status,
}


# ── Main parser entry point ──────────────────────────────────────


def parse_cli_args(argv: list[str]) -> ParsedCommand:
    """Parse raw CLI tokens into a ``ParsedCommand``.

    Handles ``--help``, ``--version``, subcommand routing,
    and flag validation via argparse.

    Parameters
    ----------
    argv:
        Command-line tokens, typically ``sys.argv[1:]``.

    Returns
    -------
    ParsedCommand
        The parsed command, flags, and positional arguments.

    Raises
    ------
    SystemExit
        On ``--help``, ``--version``, or invalid input.
    """
    if not argv or argv[0] in ("--help", "-h", "help"):
        sys.stdout.write(_format_help())
        raise SystemExit(0)

    if argv[0] in ("--version", "version"):
        sys.stdout.write(f"dark-factory, version {__version__}\n")
        raise SystemExit(0)

    command = argv[0]
    rest = argv[1:]

    if command not in COMMAND_TABLE:
        sys.stderr.write(f"Error: No such command '{command}'.\n")
        raise SystemExit(2)

    sub_parser = _SUBCOMMAND_PARSERS.get(command)
    if sub_parser is not None:
        return sub_parser(rest)

    return ParsedCommand(command=command, flags={}, args=tuple(rest))
