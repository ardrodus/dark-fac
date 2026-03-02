"""Click CLI commands for dark-factory (backward compatibility).

This module provides the Click-based CLI interface used by existing
tests via ``CliRunner``.  The actual command logic lives in
:mod:`factory.cli.handlers`.
"""

from __future__ import annotations

import click

from dark_factory import __version__


@click.group()
@click.version_option(version=__version__, prog_name="dark-factory")
def cli() -> None:
    """Dark Factory \u2014 automated issue-dispatch pipeline."""


@cli.command()
@click.option("--modules", is_flag=True, help="Validate the module manifest.")
@click.option("--debug-modules", is_flag=True, help="Show which modules loaded and when.")
@click.option("--deps", is_flag=True, help="Validate the module dependency graph.")
@click.option("--lint", is_flag=True, help="Run file size compliance check.")
def doctor(*, modules: bool, debug_modules: bool, deps: bool, lint: bool) -> None:
    """Run system health checks."""
    from dark_factory.cli.handlers import run_doctor

    run_doctor(modules=modules, debug_modules=debug_modules, deps=deps, lint=lint)


@cli.command("smoke-test")
@click.argument("title", default="trivial-python-story")
def smoke_test(title: str) -> None:
    """Run a trivial Python story through the pipeline end-to-end."""
    from dark_factory.cli.handlers import run_smoke_test

    run_smoke_test(title=title)


@cli.command()
@click.option("--run-all", is_flag=True, help="Run every discovered gate in sequence.")
@click.option("--list", "list_gates", is_flag=True, help="List all registered gates with their check counts.")
@click.option("--run", "run_name", default="", help="Run a single gate by name.")
def gates(*, run_all: bool, list_gates: bool, run_name: str) -> None:
    """Discover and run validation gates."""
    from dark_factory.cli.handlers import run_gates

    run_gates(run_all=run_all, list_gates=list_gates, run_name=run_name)


@cli.command()
def selftest() -> None:
    """Run all built-in validators and report issues."""
    from dark_factory.cli.handlers import run_selftest

    run_selftest()


@cli.command()
@click.option("--epics", is_flag=True, help="Show epic-level progress.")
def status(*, epics: bool) -> None:
    """Show pipeline or epic status."""
    from dark_factory.cli.handlers import run_status

    run_status(epics=epics)
