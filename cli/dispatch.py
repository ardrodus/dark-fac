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

    from dark_factory.cli.parser import ParsedCommand

logger = logging.getLogger(__name__)


def dispatch_config(parsed: ParsedCommand) -> None:
    """Dispatch the ``config`` command."""
    from dark_factory.cli.handlers import run_config

    action = parsed.args[0] if parsed.args else ""
    key = parsed.args[1] if len(parsed.args) > 1 else ""
    value = parsed.args[2] if len(parsed.args) > 2 else ""
    run_config(action=action, key=key, value=value)


def dispatch_dashboard(parsed: ParsedCommand) -> None:
    """Dispatch the ``dashboard`` command."""
    from dark_factory.cli.handlers import run_dashboard

    run_dashboard()


def dispatch_doctor(parsed: ParsedCommand) -> None:
    """Dispatch the ``doctor`` command."""
    from dark_factory.cli.handlers import run_doctor

    run_doctor(
        modules=parsed.flags.get("modules", False),
        debug_modules=parsed.flags.get("debug_modules", False),
        deps=parsed.flags.get("deps", False),
        lint=parsed.flags.get("lint", False),
    )


def dispatch_ingest(parsed: ParsedCommand) -> None:
    """Dispatch the ``ingest`` command."""
    from dark_factory.cli.handlers import run_ingest

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
    from dark_factory.cli.handlers import run_gates

    run_name = parsed.args[0] if parsed.args else ""
    run_gates(
        run_all=parsed.flags.get("run_all", False),
        list_gates=parsed.flags.get("list_gates", False),
        run_name=run_name,
    )


def dispatch_smoke_test(parsed: ParsedCommand) -> None:
    """Dispatch the ``smoke-test`` command."""
    from dark_factory.cli.handlers import run_smoke_test

    title = parsed.args[0] if parsed.args else "trivial-python-story"
    run_smoke_test(title=title)


def dispatch_status(parsed: ParsedCommand) -> None:
    """Dispatch the ``status`` command."""
    from dark_factory.cli.handlers import run_status

    run_status(epics=parsed.flags.get("epics", False))


def dispatch_onboard(parsed: ParsedCommand) -> None:
    """Dispatch the ``onboard`` command."""
    from dark_factory.cli.handlers import run_onboard

    run_onboard(self_onboard=parsed.flags.get("self", False))


def dispatch_selftest(parsed: ParsedCommand) -> None:
    """Dispatch the ``selftest`` command."""
    from dark_factory.cli.handlers import run_selftest

    run_selftest()


def dispatch_auto(parsed: ParsedCommand) -> None:
    """Dispatch the ``auto`` command (``--auto`` / ``-a`` flag).

    Runs the full 4-pillar auto-mode loop: poll for issues → Dark Forge →
    Crucible → Deploy → Ouroboros.
    """
    import os  # noqa: PLC0415

    from dark_factory.core.instance_lock import InstanceLockError, instance_lock  # noqa: PLC0415
    from dark_factory.modes.auto import AutoModeConfig, run_auto_mode  # noqa: PLC0415

    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        from dark_factory.core.config_manager import load_config  # noqa: PLC0415

        cfg = load_config()
        repos = cfg.data.get("repos", [])
        for r in repos:
            if isinstance(r, dict) and r.get("active"):
                repo = r.get("name", "")
                break

    try:
        with instance_lock():
            run_auto_mode(AutoModeConfig(repo=repo))
    except InstanceLockError as exc:
        sys.stderr.write(f"[instance-lock] ERROR: {exc}\n")
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        sys.stderr.write("Auto mode interrupted\n")
        raise SystemExit(130) from None


def dispatch_test(parsed: ParsedCommand) -> None:
    """Dispatch the ``test`` command (``--test <PR>`` flag)."""
    import shutil  # noqa: PLC0415

    from dark_factory.crucible.orchestrator import (  # noqa: PLC0415
        CrucibleConfig,
        CrucibleVerdict,
        run_crucible,
    )
    from dark_factory.ui.cli_colors import print_error  # noqa: PLC0415
    from dark_factory.workspace.manager import Workspace  # noqa: PLC0415

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

    from dark_factory.core.config_manager import load_config  # noqa: PLC0415

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


def _needs_onboarding() -> bool:
    """Return True if onboarding hasn't completed yet.

    Onboarding is considered complete when config.json exists AND contains
    at least one repo entry in ``owner/repo`` format (not a local path).
    """
    import json  # noqa: PLC0415

    from dark_factory.core.config_manager import resolve_config_path  # noqa: PLC0415

    config_path = resolve_config_path()
    if not config_path.is_file():
        return True
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    repos = data.get("repos", [])
    if not isinstance(repos, list) or not repos:
        return True
    # Check that at least one active repo is in owner/repo format
    for repo in repos:
        if isinstance(repo, dict) and repo.get("active"):
            name = repo.get("name", "")
            if isinstance(name, str) and "/" in name and ":\\" not in name:
                return False
    return True


def _run_subsystem(key: str) -> None:
    """Launch the subsystem selected from the interactive TUI menu."""
    if key == "1":
        # Dark Forge — run one dispatch cycle through the full pipeline
        _run_forge_interactive()
    elif key == "2":
        # Crucible — prompt for PR number and validate
        _run_crucible_interactive()
    elif key == "3":
        # Ouroboros — runs automatically in auto mode
        sys.stdout.write(
            "\n  Ouroboros runs automatically after each auto-mode cycle.\n"
            "  Use 'dark-factory auto' to start the autonomous loop.\n\n"
        )
        input("  Press Enter to return to menu...")
    elif key == "4":
        # Foundry — workspace manager TUI
        from dark_factory.modes.foundry import run_foundry_tui  # noqa: PLC0415

        run_foundry_tui()
    elif key == "5":
        # Settings — config editor TUI
        from dark_factory.modes.settings import run_settings_tui  # noqa: PLC0415

        run_settings_tui()


_forge_depth = 0  # track nested pipeline depth for indentation


def _forge_event_printer(event: object) -> None:
    """Print pipeline events to stdout for interactive progress."""
    global _forge_depth  # noqa: PLW0603
    from dark_factory.engine.events import (  # noqa: PLC0415
        ParallelCompleted,
        ParallelStarted,
        PipelineCompleted,
        PipelineStarted,
        StageCompleted,
        StageFailed,
        StageRetrying,
        StageStarted,
    )

    indent = "  " + "    " * _forge_depth

    if isinstance(event, PipelineStarted):
        if _forge_depth == 0:
            sys.stdout.write(f"\n{indent}Pipeline '{event.name}' started\n")
        else:
            sys.stdout.write(f"\n{indent}>> {event.name}\n")
        _forge_depth += 1
    elif isinstance(event, PipelineCompleted):
        _forge_depth = max(0, _forge_depth - 1)
        indent = "  " + "    " * _forge_depth
        if _forge_depth == 0:
            sys.stdout.write(f"{indent}Pipeline completed ({event.duration:.1f}s)\n\n")
        else:
            sys.stdout.write(f"{indent}<< done ({event.duration:.1f}s)\n")
    elif isinstance(event, StageStarted):
        sys.stdout.write(f"{indent}[{event.index}] {event.name} ... ")
        sys.stdout.flush()
    elif isinstance(event, StageCompleted):
        sys.stdout.write(f"done ({event.duration:.1f}s)\n")
    elif isinstance(event, StageFailed):
        retry = " (retrying)" if event.will_retry else ""
        sys.stdout.write(f"FAILED{retry}\n")
    elif isinstance(event, StageRetrying):
        sys.stdout.write(f"{indent}[{event.index}] {event.name} retry #{event.attempt}... ")
        sys.stdout.flush()
    elif isinstance(event, ParallelStarted):
        sys.stdout.write(f"{indent}parallel ({event.branch_count} branches)...\n")
    elif isinstance(event, ParallelCompleted):
        sys.stdout.write(f"{indent}parallel done ({event.duration:.1f}s)\n")


def _run_forge_interactive() -> None:
    """Pick the next queued issue and run it through Dark Forge only.

    The interactive forge command runs ONLY the Dark Forge pipeline
    (architecture review + TDD).  It does NOT run Crucible, Deploy,
    or Ouroboros -- those are separate phases triggered via auto mode
    or their own menu entries.
    """
    import os  # noqa: PLC0415
    import time  # noqa: PLC0415

    from dark_factory.modes.auto import run_dark_forge  # noqa: PLC0415

    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        from dark_factory.core.config_manager import load_config  # noqa: PLC0415

        cfg = load_config()
        for r in cfg.data.get("repos", []):
            if isinstance(r, dict) and r.get("active"):
                repo = r.get("name", "")
                break

    from dark_factory.dispatch.issue_dispatcher import select_next_issue  # noqa: PLC0415

    sys.stdout.write("\n  Dark Forge: scanning for queued issues...\n")
    issue = select_next_issue(repo=repo or None)
    if issue is None:
        sys.stdout.write("  No queued issues found.\n\n")
        input("  Press Enter to return to menu...")
        return

    # Acquire a workspace for the issue
    from dark_factory.workspace.manager import acquire_workspace  # noqa: PLC0415

    sys.stdout.write(f"  Processing #{issue.number}: {issue.title}\n")

    # GitHub lifecycle: queued → arch-review + comment
    from dark_factory.modes.auto import complete_arch_review, dispatch_to_forge  # noqa: PLC0415

    dispatch_to_forge(issue.number, repo=repo)

    try:
        workspace = acquire_workspace(repo, issue.number)
    except Exception as exc:
        sys.stdout.write(f"  Failed to acquire workspace: {exc}\n\n")
        input("  Press Enter to return to menu...")
        return

    # Load per-workspace settings
    from dark_factory.modes.foundry_onboard import load_workspace_settings  # noqa: PLC0415

    ws_settings = load_workspace_settings(workspace.path)
    skip_arch = bool(ws_settings.get("skip_arch_review", False))

    t0 = time.monotonic()
    passed = run_dark_forge(
        issue,
        workspace.path,
        skip_arch_review=skip_arch,
        on_event=_forge_event_printer,
    )
    elapsed = time.monotonic() - t0

    # GitHub lifecycle: arch-review → arch-approved (or failed) + comment
    complete_arch_review(issue.number, passed=passed, duration_s=elapsed, repo=repo)

    status = "PASSED" if passed else "FAILED"
    sys.stdout.write(f"  Dark Forge {status} ({elapsed:.1f}s)\n\n")
    input("  Press Enter to return to menu...")


def _run_crucible_interactive() -> None:
    """Prompt for a PR number and run Crucible validation."""
    import shutil  # noqa: PLC0415

    sys.stdout.write("\n")
    try:
        raw = input("  Enter PR number to validate: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not raw.isdigit() or int(raw) <= 0:
        sys.stdout.write("  Invalid PR number.\n\n")
        input("  Press Enter to return to menu...")
        return

    if not shutil.which("docker"):
        sys.stdout.write("  Crucible requires Docker but 'docker' was not found.\n\n")
        input("  Press Enter to return to menu...")
        return

    pr_number = int(raw)
    from dark_factory.crucible.orchestrator import CrucibleConfig, run_crucible  # noqa: PLC0415
    from dark_factory.workspace.manager import Workspace  # noqa: PLC0415

    from dark_factory.core.config_manager import load_config  # noqa: PLC0415

    config = load_config()
    repo_root = config.data.get("project", {}).get("repo_root", "")
    if not repo_root:
        from pathlib import Path  # noqa: PLC0415

        repo_root = str(Path.cwd())

    ws = Workspace(
        name=f"crucible-pr-{pr_number}", path=repo_root,
        repo_url="", branch=f"pr-{pr_number}",
    )
    sys.stdout.write(f"  Crucible: validating PR #{pr_number}...\n")
    result = run_crucible(ws, CrucibleConfig(), issue_number=pr_number)
    sys.stdout.write(
        f"  Verdict: {result.verdict.value}\n"
        f"  pass={result.pass_count} fail={result.fail_count} "
        f"skip={result.skip_count} ({result.duration_s:.1f}s)\n"
    )
    if result.error:
        sys.stdout.write(f"  Error: {result.error}\n")
    sys.stdout.write("\n")
    input("  Press Enter to return to menu...")


def dispatch_interactive(parsed: ParsedCommand) -> None:
    """Dispatch the ``interactive`` command (default when no args).

    On first run — or if a previous onboarding didn't complete — automatically
    triggers onboarding before entering the Textual TUI menu loop.
    """
    if _needs_onboarding():
        sys.stdout.write("First run detected \u2014 starting onboarding...\n\n")
        from dark_factory.setup.orchestrator import run_onboarding  # noqa: PLC0415

        rc = run_onboarding()
        if rc != 0:
            raise SystemExit(rc)

    from dark_factory.core.instance_lock import InstanceLockError, instance_lock  # noqa: PLC0415
    from dark_factory.modes.interactive import run_interactive_tui  # noqa: PLC0415

    try:
        with instance_lock():
            while True:
                selection = run_interactive_tui()
                if selection is None:
                    break
                _run_subsystem(selection)
    except InstanceLockError as exc:
        sys.stderr.write(f"[instance-lock] ERROR: {exc}\n")
        raise SystemExit(1) from None


def dispatch_workspace(parsed: ParsedCommand) -> None:
    """Dispatch the ``workspace`` command."""
    from dark_factory.cli.handlers import run_workspace

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
        from dark_factory.ui.cli_colors import print_error  # noqa: PLC0415

        known = ", ".join(sorted(DISPATCH_TABLE))
        print_error(
            f"Unknown command: '{parsed.command}'",
            hint=f"Available commands: {known}",
        )
        raise SystemExit(1)
