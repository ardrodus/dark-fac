"""Command dispatch — entry points for all Dark Factory pipelines.

Architecture: Invoke → Gather Dependencies → Execute
=======================================================

Every pipeline entry point in this module follows the same three-phase
pattern.  This is intentional and MUST be preserved when adding new
pipelines or modifying existing ones.

**Phase 1 — Invoke** (this module):
    Resolve the active repo, validate inputs, and acquire a workspace.
    Python code here is THIN — just enough to wire inputs to the engine.

**Phase 2 — Gather Dependencies** (workspace/manager.py):
    ``acquire_workspace()`` clones or pulls the repo, then bootstraps
    agents, DOT pipelines, scripts, and logs into a self-contained
    workspace directory.  The engine finds everything it needs inside.

**Phase 3 — Execute** (pipeline/engine.py → pipelines/*.dot):
    ``engine.run_pipeline(name, context)`` loads a DOT file and walks
    the graph.  Each node is an LLM agent prompt.  ALL workflow logic,
    branching, retries, and decisions live in the DOT file — not in
    Python.

Why this matters
----------------
Complexity belongs in the DOT files, not in Python dispatch code.
DOT pipelines are declarative, versionable, and workspace-scoped —
every workspace gets its own copy.  If you are tempted to add
conditional logic, retries, or multi-step orchestration in Python,
**stop and put it in a DOT file instead**.  Python's only job is:
resolve repo → acquire workspace → call ``engine.run_pipeline()``.

Adding a new pipeline
---------------------
1. Create ``pipelines/<name>.dot`` with the workflow graph.
2. Add a dispatch function here that follows the pattern::

       repo = _resolve_active_repo()
       workspace = acquire_workspace(repo, identifier)
       result = engine.run_pipeline("<name>", {
           "workspace_root": workspace.path, ...
       })

3. Register it in ``DISPATCH_TABLE`` and ``cli/parser.py``.
4. Do NOT add workflow logic in the dispatch function.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _CrucibleOutcome:
    """Lightweight crucible result for verdict handling."""

    verdict: str  # "go", "no_go", "needs_live"
    error: str = ""
    duration_s: float = 0.0


def _extract_crucible_verdict(result: object) -> _CrucibleOutcome:
    """Extract verdict from a PipelineResult's completed_nodes."""
    from dark_factory.engine.runner import PipelineStatus  # noqa: PLC0415

    status = getattr(result, "status", None)
    completed = getattr(result, "completed_nodes", [])
    error = getattr(result, "error", "") or ""
    duration = getattr(result, "duration_seconds", 0.0)

    if status == PipelineStatus.FAILED:
        verdict = "no_go"
    elif "needs_live" in completed:
        verdict = "needs_live"
    elif "no_go" in completed or "smoke_failed" in completed:
        verdict = "no_go"
    elif "go" in completed:
        verdict = "go"
    else:
        verdict = "no_go"

    return _CrucibleOutcome(verdict=verdict, error=error, duration_s=duration)


def dispatch_test(parsed: ParsedCommand) -> None:
    """Dispatch the ``test`` command (``--test <PR>`` flag).

    Follows the prepare → gather deps → execute pattern:
      1. Resolve active repo
      2. acquire_workspace() — clone/pull, bootstrap agents/pipelines
      3. engine.run_pipeline("crucible", {workspace_root, pr_number, pr_branch})
      4. Print verdict, exit 1 on failure
    """
    import asyncio  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415
    from dark_factory.ui.cli_colors import print_error  # noqa: PLC0415
    from dark_factory.workspace.manager import acquire_workspace  # noqa: PLC0415

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

    repo = _resolve_active_repo()
    if not repo:
        print_error(
            "No active repo configured",
            hint="Run 'dark-factory onboard' first or set GITHUB_REPO env var.",
        )
        raise SystemExit(1)

    sys.stdout.write(f"Crucible: acquiring workspace for PR #{pr_number}...\n")
    try:
        workspace = acquire_workspace(repo, pr_number)
    except Exception as exc:
        print_error(f"Failed to acquire workspace: {exc}")
        raise SystemExit(1) from None

    sys.stdout.write(f"Crucible: validating PR #{pr_number}...\n")
    engine = FactoryPipelineEngine()
    pipeline_result = asyncio.run(engine.run_pipeline("crucible", {
        "workspace_root": workspace.path,
        "pr_number": str(pr_number),
        "pr_branch": f"pr-{pr_number}",
    }))
    outcome = _extract_crucible_verdict(pipeline_result)
    sys.stdout.write(f"Crucible verdict: {outcome.verdict}\n")
    sys.stdout.write(f"  duration={outcome.duration_s:.1f}s\n")
    if outcome.error:
        sys.stdout.write(f"  error: {outcome.error}\n")
    if outcome.verdict != "go":
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


def _resolve_active_repo() -> str:
    """Return the active repo name from GITHUB_REPO env var or config."""
    import os  # noqa: PLC0415

    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        from dark_factory.core.config_manager import load_config  # noqa: PLC0415

        cfg = load_config()
        for r in cfg.data.get("repos", []):
            if isinstance(r, dict) and r.get("active"):
                repo = r.get("name", "")
                break
    return repo


def _run_subsystem(key: str) -> None:
    """Launch the subsystem selected from the interactive TUI menu."""
    if key == "1":
        # Dark Forge — run one dispatch cycle through the full pipeline
        _run_forge_interactive()
    elif key == "2":
        # Crucible — prompt for PR number and validate
        _run_crucible_interactive()
    elif key == "3":
        # Foundry — workspace manager TUI
        from dark_factory.modes.foundry import run_foundry_tui  # noqa: PLC0415

        run_foundry_tui()
    elif key == "4":
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
    """Pick the next queued issue and run it through Dark Forge.

    Follows the prepare → gather deps → execute pattern:
      1. Resolve active repo
      2. Poll for queued issue
      3. acquire_workspace() — clone/pull, bootstrap agents/pipelines
      4. engine.run_pipeline("dark_forge", {workspace_root, issue})
      5. Label issue done/failed
    """
    import asyncio  # noqa: PLC0415
    import time  # noqa: PLC0415

    from dark_factory.dispatch.issue_dispatcher import (  # noqa: PLC0415
        LABEL_DONE,
        LABEL_FAILED,
        LABEL_IN_PROGRESS,
        LABEL_QUEUED,
        select_next_issue,
    )
    from dark_factory.integrations.gh_safe import add_label, remove_label  # noqa: PLC0415
    from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415
    from dark_factory.workspace.manager import acquire_workspace  # noqa: PLC0415

    repo = _resolve_active_repo()

    sys.stdout.write("\n  Dark Forge: scanning for queued issues...\n")
    issue = select_next_issue(repo=repo or None)
    if issue is None:
        sys.stdout.write("  No queued issues found.\n\n")
        input("  Press Enter to return to menu...")
        return

    sys.stdout.write(f"  Processing #{issue.number}: {issue.title}\n")

    # Label transition: queued → in-progress
    try:
        remove_label(issue.number, LABEL_QUEUED, repo=repo or None)
    except Exception:  # noqa: BLE001
        pass
    add_label(issue.number, LABEL_IN_PROGRESS, repo=repo or None)

    try:
        workspace = acquire_workspace(repo, issue.number)
    except Exception as exc:
        sys.stdout.write(f"  Failed to acquire workspace: {exc}\n\n")
        add_label(issue.number, LABEL_FAILED, repo=repo or None)
        input("  Press Enter to return to menu...")
        return

    issue_dict = {
        "number": issue.number,
        "title": issue.title,
        "body": getattr(issue, "body", ""),
        "labels": getattr(issue, "labels", []),
    }

    engine = FactoryPipelineEngine(on_event=_forge_event_printer)
    t0 = time.monotonic()
    try:
        asyncio.run(engine.run_pipeline("dark_forge", {
            "workspace_root": workspace.path,
            "issue": issue_dict,
            "issue_number": str(issue.number),
        }))
        passed = True
    except Exception as exc:  # noqa: BLE001
        sys.stdout.write(f"  Pipeline error: {exc}\n")
        passed = False
    elapsed = time.monotonic() - t0

    # Label transition: in-progress → done/failed
    try:
        remove_label(issue.number, LABEL_IN_PROGRESS, repo=repo or None)
    except Exception:  # noqa: BLE001
        pass
    add_label(issue.number, LABEL_DONE if passed else LABEL_FAILED, repo=repo or None)

    status = "PASSED" if passed else "FAILED"
    sys.stdout.write(f"  Dark Forge {status} ({elapsed:.1f}s)\n\n")
    input("  Press Enter to return to menu...")


def _run_crucible_interactive() -> None:
    """Prompt for a PR number and run Crucible validation via the DOT engine.

    Follows the prepare → gather deps → execute pattern:
      1. Resolve active repo
      2. Prompt for PR number
      3. acquire_workspace() — clone/pull, bootstrap agents/pipelines
      4. engine.run_pipeline("crucible", {workspace_root, pr_number, pr_branch})
      5. Print verdict
    """
    import asyncio  # noqa: PLC0415
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
    repo = _resolve_active_repo()
    if not repo:
        sys.stdout.write("  No active repo configured.\n\n")
        input("  Press Enter to return to menu...")
        return

    from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415
    from dark_factory.workspace.manager import acquire_workspace  # noqa: PLC0415

    sys.stdout.write(f"  Crucible: acquiring workspace for PR #{pr_number}...\n")
    try:
        workspace = acquire_workspace(repo, pr_number)
    except Exception as exc:
        sys.stdout.write(f"  Failed to acquire workspace: {exc}\n\n")
        input("  Press Enter to return to menu...")
        return

    sys.stdout.write(f"  Crucible: validating PR #{pr_number}...\n")
    engine = FactoryPipelineEngine()
    pipeline_result = asyncio.run(engine.run_pipeline("crucible", {
        "workspace_root": workspace.path,
        "pr_number": str(pr_number),
        "pr_branch": f"pr-{pr_number}",
    }))
    outcome = _extract_crucible_verdict(pipeline_result)
    sys.stdout.write(
        f"  Verdict: {outcome.verdict}\n"
        f"  duration={outcome.duration_s:.1f}s\n"
    )
    if outcome.error:
        sys.stdout.write(f"  Error: {outcome.error}\n")
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
