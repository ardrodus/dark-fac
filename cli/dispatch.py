"""Command dispatch for the argparse-based CLI path.

Maps parsed commands to their handler implementations in
:mod:`factory.cli.handlers`.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.cli.parser import ParsedCommand

logger = logging.getLogger(__name__)


def dispatch_config(parsed: ParsedCommand) -> None:
    """Dispatch the ``config`` command."""
    from factory.cli.handlers import run_config

    action = parsed.args[0] if parsed.args else ""
    key = parsed.args[1] if len(parsed.args) > 1 else ""
    value = parsed.args[2] if len(parsed.args) > 2 else ""
    run_config(action=action, key=key, value=value)


def dispatch_dashboard(parsed: ParsedCommand) -> None:
    """Dispatch the ``dashboard`` command."""
    from factory.cli.handlers import run_dashboard

    run_dashboard()


def dispatch_doctor(parsed: ParsedCommand) -> None:
    """Dispatch the ``doctor`` command."""
    from factory.cli.handlers import run_doctor

    run_doctor(
        modules=parsed.flags.get("modules", False),
        debug_modules=parsed.flags.get("debug_modules", False),
        deps=parsed.flags.get("deps", False),
        lint=parsed.flags.get("lint", False),
    )


def dispatch_ingest(parsed: ParsedCommand) -> None:
    """Dispatch the ``ingest`` command."""
    from factory.cli.handlers import run_ingest

    prd_path = parsed.args[0] if parsed.args else ""
    repo = parsed.args[1] if len(parsed.args) > 1 else ""
    run_ingest(
        prd_path=prd_path,
        repo=repo,
        validate_only=parsed.flags.get("validate", False),
        force=parsed.flags.get("force", False),
        auto_split=parsed.flags.get("auto_split", False),
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

    run_status(epics=parsed.flags.get("epics", False))


def dispatch_onboard(parsed: ParsedCommand) -> None:
    """Dispatch the ``onboard`` command."""
    from factory.cli.handlers import run_onboard

    run_onboard(self_onboard=parsed.flags.get("self", False))


def dispatch_selftest(parsed: ParsedCommand) -> None:
    """Dispatch the ``selftest`` command."""
    from factory.cli.handlers import run_selftest

    run_selftest()


def dispatch_auto(parsed: ParsedCommand) -> None:
    """Dispatch the ``auto`` command (``--auto`` / ``-a`` flag)."""
    from factory.core.instance_lock import InstanceLockError, instance_lock  # noqa: PLC0415
    from factory.dispatch.issue_dispatcher import DispatcherState, auto_main_loop  # noqa: PLC0415

    dev = parsed.flags.get("dev_mode", False)

    try:
        with instance_lock():
            auto_main_loop(state=DispatcherState(dev_mode=dev))
    except InstanceLockError as exc:
        sys.stderr.write(f"[instance-lock] ERROR: {exc}\n")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        sys.stderr.write("Dispatch interrupted\n")
        raise SystemExit(130) from None


def dispatch_test(parsed: ParsedCommand) -> None:
    """Dispatch the ``test`` command (``--test <PR>`` flag)."""
    import shutil  # noqa: PLC0415

    from factory.crucible.orchestrator import CrucibleConfig, CrucibleVerdict, run_crucible  # noqa: PLC0415
    from factory.ui.cli_colors import print_error  # noqa: PLC0415
    from factory.workspace.manager import Workspace  # noqa: PLC0415

    pr_number = int(parsed.args[0]) if parsed.args else 0
    if pr_number <= 0:
        print_error(
            "--test requires a valid PR number",
            hint="Usage: dark-factory --test <PR_NUMBER>",
        )
        raise SystemExit(2)

    if not shutil.which("docker"):
        print_error(
            "Crucible requires Docker but 'docker' was not found on PATH",
            hint="Install Docker: https://docs.docker.com/get-docker/",
        )
        raise SystemExit(1)

    from factory.core.config_manager import load_config  # noqa: PLC0415

    config = load_config()
    repo_root = config.data.get("project", {}).get("repo_root", "")
    if not repo_root:
        from pathlib import Path  # noqa: PLC0415

        repo_root = str(Path.cwd())

    ws = Workspace(name=f"crucible-pr-{pr_number}", path=repo_root, repo_url="", branch=f"pr-{pr_number}")
    sys.stdout.write(f"Crucible: re-running validation for PR #{pr_number}\n")

    result = run_crucible(ws, CrucibleConfig(), issue_number=pr_number)
    sys.stdout.write(f"Crucible verdict: {result.verdict.value}\n")
    sys.stdout.write(f"  pass={result.pass_count} fail={result.fail_count} skip={result.skip_count}\n")
    sys.stdout.write(f"  duration={result.duration_s:.1f}s\n")
    if result.error:
        sys.stdout.write(f"  error: {result.error}\n")
    if result.verdict != CrucibleVerdict.GO:
        raise SystemExit(1)


def dispatch_interactive(parsed: ParsedCommand) -> None:
    """Dispatch the ``interactive`` command (default when no args)."""
    from factory.core.instance_lock import InstanceLockError, instance_lock  # noqa: PLC0415
    from factory.ui.interactive_menu import run_interactive  # noqa: PLC0415

    try:
        with instance_lock():
            run_interactive()
    except InstanceLockError as exc:
        sys.stderr.write(f"[instance-lock] ERROR: {exc}\n")
        raise SystemExit(1) from None


def dispatch_workspace(parsed: ParsedCommand) -> None:
    """Dispatch the ``workspace`` command."""
    from factory.cli.handlers import run_workspace

    action = parsed.args[0] if parsed.args else ""
    name = parsed.args[1] if len(parsed.args) > 1 else ""
    run_workspace(action=action, name=name)


DISPATCH_TABLE: dict[str, Callable[[ParsedCommand], None]] = {
    "auto": dispatch_auto,
    "config": dispatch_config,
    "dashboard": dispatch_dashboard,
    "doctor": dispatch_doctor,
    "gates": dispatch_gates,
    "ingest": dispatch_ingest,
    "interactive": dispatch_interactive,
    "onboard": dispatch_onboard,
    "selftest": dispatch_selftest,
    "smoke-test": dispatch_smoke_test,
    "status": dispatch_status,
    "test": dispatch_test,
    "workspace": dispatch_workspace,
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
        from factory.ui.cli_colors import print_error  # noqa: PLC0415

        known = ", ".join(sorted(DISPATCH_TABLE))
        print_error(
            f"Unknown command: '{parsed.command}'",
            hint=f"Available commands: {known}",
        )
        raise SystemExit(1)
