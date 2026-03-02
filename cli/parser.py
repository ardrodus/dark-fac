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
    "config": "Read and write .dark-factory/config.json values.",
    "dashboard": "Launch the Textual TUI dashboard.",
    "doctor": "Run system health checks.",
    "gates": "Discover and run validation gates.",
    "ingest": "Ingest a PRD file, validate stories, and create GitHub Issues.",
    "onboard": "Run project onboarding (use --self for factory self-onboarding).",
    "selftest": "Run all built-in validators and report issues.",
    "smoke-test": "Run a trivial Python story through the pipeline end-to-end.",
    "status": "Show pipeline, epic, or bootstrap status.",
    "update": "Check for updates, apply, rollback, or toggle auto-update.",
    "workspace": "Manage workspaces (list, clean, purge, stats).",
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
        "  -a, --auto       Start autonomous dispatch mode.",
        "  -b, --bootstrap  Run bootstrap mode (plan/implement/test only).",
        "  -d, --dev        Dev mode -- use LocalStack instead of real AWS.",
        "  -t, --test <PR>  Re-run Crucible validation for a specific PR.",
        "  --version        Show the version and exit.",
        "  --help           Show this message and exit.",
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


def _parse_ingest(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory ingest`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory ingest",
        description="Ingest a PRD file, validate stories, and create GitHub Issues.",
    )
    parser.add_argument("--prd", required=True, help="Path to a PRD file (JSON or Markdown).")
    parser.add_argument("--repo", default="", help="GitHub repo (owner/name) for issue creation.")
    parser.add_argument("--validate", action="store_true", help="Dry-run: validate without creating issues.")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompts.")
    parser.add_argument("--auto-split", action="store_true", dest="auto_split",
                        help="Auto-split oversized stories (>10 AC items).")
    ns = parser.parse_args(argv)
    return ParsedCommand(
        command="ingest",
        flags={"validate": ns.validate, "force": ns.force, "auto_split": ns.auto_split},
        args=(resolve_home(ns.prd), ns.repo),
    )


def _parse_onboard(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory onboard`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory onboard",
        description="Run project onboarding (use --self for factory self-onboarding).",
    )
    parser.add_argument(
        "--self",
        action="store_true",
        dest="self_onboard",
        help="Run self-onboarding for the factory itself.",
    )
    ns = parser.parse_args(argv)
    return ParsedCommand(
        command="onboard",
        flags={"self": ns.self_onboard},
        args=(),
    )


def _parse_selftest(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory selftest`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory selftest",
        description="Run all built-in validators and report issues.",
    )
    parser.parse_args(argv)
    return ParsedCommand(command="selftest", flags={}, args=())


def _parse_config(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory config`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory config",
        description="Read and write .dark-factory/config.json values.",
    )
    sub = parser.add_subparsers(dest="action")
    sub.required = True
    sp = sub.add_parser("set", help="Set a config value.")
    sp.add_argument("key", help="Config key (dot notation for nesting).")
    sp.add_argument("value", help="Value to set.")
    sp = sub.add_parser("get", help="Get a config value.")
    sp.add_argument("key", help="Config key (dot notation for nesting).")
    sub.add_parser("list", help="List all config keys and values.")
    ns = parser.parse_args(argv)
    if ns.action == "set":
        return ParsedCommand(command="config", flags={}, args=("set", ns.key, ns.value))
    if ns.action == "get":
        return ParsedCommand(command="config", flags={}, args=("get", ns.key))
    return ParsedCommand(command="config", flags={}, args=("list",))


def _parse_dashboard(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory dashboard`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory dashboard",
        description="Launch the Textual TUI dashboard.",
    )
    parser.parse_args(argv)
    return ParsedCommand(command="dashboard", flags={}, args=())


def _parse_update(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory update`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory update",
        description="Check for updates, apply, rollback, or toggle auto-update.",
    )
    parser.add_argument("--check", action="store_true", help="Check for available updates.")
    parser.add_argument("--apply", default="", help="Apply update for a specific tag.")
    parser.add_argument("--rollback", action="store_true", help="Rollback to previous version.")
    parser.add_argument("--enable", action="store_true", help="Enable auto-update checks.")
    parser.add_argument("--disable", action="store_true", help="Disable auto-update checks.")
    ns = parser.parse_args(argv)
    return ParsedCommand(
        command="update",
        flags={
            "check": ns.check,
            "rollback": ns.rollback,
            "enable": ns.enable,
            "disable": ns.disable,
        },
        args=(ns.apply,) if ns.apply else (),
    )


def _parse_workspace(argv: list[str]) -> ParsedCommand:
    """Parse ``dark-factory workspace`` arguments."""
    parser = argparse.ArgumentParser(
        prog="dark-factory workspace",
        description="Manage workspaces (list, clean, purge, stats).",
    )
    sub = parser.add_subparsers(dest="action")
    sub.required = True
    sub.add_parser("list", help="Show all cached workspaces.")
    sp = sub.add_parser("clean", help="Remove a specific workspace.")
    sp.add_argument("name", help="Workspace name to clean.")
    sub.add_parser("purge", help="Remove all workspaces.")
    sub.add_parser("stats", help="Show workspace statistics.")
    ns = parser.parse_args(argv)
    if ns.action == "clean":
        return ParsedCommand(command="workspace", flags={}, args=("clean", ns.name))
    return ParsedCommand(command="workspace", flags={}, args=(ns.action,))


_SUBCOMMAND_PARSERS: dict[str, Callable[[list[str]], ParsedCommand]] = {
    "config": _parse_config,
    "dashboard": _parse_dashboard,
    "doctor": _parse_doctor,
    "gates": _parse_gates,
    "ingest": _parse_ingest,
    "onboard": _parse_onboard,
    "selftest": _parse_selftest,
    "smoke-test": _parse_smoke_test,
    "status": _parse_status,
    "update": _parse_update,
    "workspace": _parse_workspace,
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
    if not argv:
        return ParsedCommand(command="interactive", flags={}, args=())

    if argv[0] in ("--help", "-h", "help"):
        sys.stdout.write(_format_help())
        raise SystemExit(0)

    if argv[0] in ("--version", "version"):
        sys.stdout.write(f"dark-factory, version {__version__}\n")
        raise SystemExit(0)

    # Parse top-level flags (--auto, --bootstrap, --dev, --test) that can be combined
    has_auto = False
    has_bootstrap = False
    has_dev = False
    has_test = False
    test_pr = ""
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in ("--auto", "-a"):
            has_auto = True
        elif token in ("--bootstrap", "-b"):
            has_bootstrap = True
        elif token in ("--dev", "-d"):
            has_dev = True
        elif token in ("--test", "-t"):
            has_test = True
            if i + 1 < len(argv):
                test_pr = argv[i + 1]
                i += 1
            else:
                sys.stderr.write("Error: --test requires a PR number argument.\n")
                raise SystemExit(2)
        else:
            remaining.append(token)
        i += 1

    if (has_auto or has_dev or has_bootstrap or has_test) and remaining:
        sys.stderr.write("Error: --auto/--bootstrap/--dev/--test cannot be combined with a subcommand.\n")
        raise SystemExit(2)

    if has_auto and has_bootstrap:
        sys.stderr.write("Error: --auto and --bootstrap cannot be combined.\n")
        raise SystemExit(2)

    if has_test and (has_auto or has_bootstrap):
        sys.stderr.write("Error: --test cannot be combined with --auto or --bootstrap.\n")
        raise SystemExit(2)

    if has_test:
        try:
            pr_number = int(test_pr)
        except ValueError:
            sys.stderr.write(f"Error: --test requires a valid integer PR number, got '{test_pr}'.\n")
            raise SystemExit(2) from None
        if pr_number <= 0:
            sys.stderr.write(f"Error: PR number must be positive, got {pr_number}.\n")
            raise SystemExit(2)
        return ParsedCommand(command="test", flags={"dev_mode": has_dev}, args=(str(pr_number),))

    if has_auto:
        return ParsedCommand(command="auto", flags={"dev_mode": has_dev}, args=())

    if has_bootstrap:
        return ParsedCommand(command="bootstrap", flags={"dev_mode": has_dev}, args=())

    if has_dev:
        return ParsedCommand(command="interactive", flags={"dev_mode": True}, args=())

    command = argv[0]
    rest = argv[1:]

    if command not in COMMAND_TABLE:
        sys.stderr.write(f"Error: No such command '{command}'.\n")
        raise SystemExit(2)

    sub_parser = _SUBCOMMAND_PARSERS.get(command)
    if sub_parser is not None:
        return sub_parser(rest)

    return ParsedCommand(command=command, flags={}, args=tuple(rest))
