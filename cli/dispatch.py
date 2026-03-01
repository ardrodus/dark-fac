"""Command dispatch for the argparse-based CLI path.

Maps parsed commands to their handler implementations in
:mod:`factory.cli.handlers`.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.cli.parser import ParsedCommand


def dispatch_doctor(parsed: ParsedCommand) -> None:
    """Dispatch the ``doctor`` command."""
    from factory.cli.handlers import run_doctor

    run_doctor(
        migration=parsed.flags.get("migration", False),
        modules=parsed.flags.get("modules", False),
        debug_modules=parsed.flags.get("debug_modules", False),
        deps=parsed.flags.get("deps", False),
        lint=parsed.flags.get("lint", False),
    )


def dispatch_gates(parsed: ParsedCommand) -> None:
    """Dispatch the ``gates`` command."""
    from factory.cli.handlers import run_gates

    run_name = parsed.args[0] if parsed.args else ""
    run_gates(
        run_all=parsed.flags.get("run_all", False),
        list_gates=parsed.flags.get("list_gates", False),
        run_name=run_name,
    )


def dispatch_smoke_test(parsed: ParsedCommand) -> None:
    """Dispatch the ``smoke-test`` command."""
    from factory.cli.handlers import run_smoke_test

    title = parsed.args[0] if parsed.args else "trivial-python-story"
    run_smoke_test(title=title)


def dispatch_status(parsed: ParsedCommand) -> None:
    """Dispatch the ``status`` command."""
    from factory.cli.handlers import run_status

    run_status(
        epics=parsed.flags.get("epics", False),
        bootstrap=parsed.flags.get("bootstrap", False),
    )


def dispatch_selftest(parsed: ParsedCommand) -> None:
    """Dispatch the ``selftest`` command."""
    from factory.cli.handlers import run_selftest

    run_selftest()


DISPATCH_TABLE: dict[str, Callable[[ParsedCommand], None]] = {
    "doctor": dispatch_doctor,
    "gates": dispatch_gates,
    "selftest": dispatch_selftest,
    "smoke-test": dispatch_smoke_test,
    "status": dispatch_status,
}


def dispatch(parsed: ParsedCommand) -> None:
    """Look up and invoke the handler for a parsed command.

    Parameters
    ----------
    parsed:
        The result of :func:`factory.cli.parser.parse_cli_args`.

    Raises
    ------
    SystemExit
        If no handler exists for the parsed command.
    """
    handler = DISPATCH_TABLE.get(parsed.command)
    if handler is not None:
        handler(parsed)
    else:
        sys.stderr.write(f"No handler for command: {parsed.command}\n")
        raise SystemExit(1)
