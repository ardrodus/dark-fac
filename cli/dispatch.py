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


def _build_crucible_context(
    repo: str,
    workspace_path: str,
    pr_number: int,
) -> dict:
    """Build the context dict for engine.run_pipeline("crucible", ctx).

    Resolves workspace_config from config.json to populate all context
    variables referenced by crucible.dot ($workspace, $app_type, $language,
    $framework, $test_cmd, $test_repo, $pr_number, $pr_branch).
    """
    ws_cfg = _resolve_workspace_config(repo)
    analysis = ws_cfg.get("analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
    crucible_cfg = ws_cfg.get("crucible", {})
    if not isinstance(crucible_cfg, dict):
        crucible_cfg = {}

    return {
        "workspace": workspace_path,
        "pr_number": str(pr_number),
        "pr_branch": f"pr-{pr_number}",
        "app_type": str(ws_cfg.get("app_type", analysis.get("detected_app_type", "console"))),
        "language": str(analysis.get("language", "")),
        "framework": str(analysis.get("framework", "")),
        "test_cmd": str(analysis.get("test_cmd", "")),
        "test_repo": str(crucible_cfg.get("test_repo", "")),
    }


def dispatch_test(parsed: ParsedCommand) -> None:
    """Dispatch the ``test`` command (``--test <PR>`` flag).

    Follows the Invoke → Gather → Execute pattern:
      1. Resolve active repo + workspace config (app_type, language, etc.)
      2. acquire_workspace() — clone/pull, bootstrap agents/pipelines
      3. engine.run_pipeline("crucible", full context dict)
      4. Print verdict, exit 1 on failure
    """
    import asyncio  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415
    from dark_factory.ui.cli_colors import cprint, print_error  # noqa: PLC0415
    from dark_factory.workspace.manager import acquire_workspace  # noqa: PLC0415

    pr_number = int(parsed.args[0]) if parsed.args else 0
    if pr_number <= 0:
        print_error(
            "--test requires a valid PR number",
            hint="Usage: dark-factory --test <PR_NUMBER>",
        )
        raise SystemExit(2)

    repo = _resolve_active_repo()
    if not repo:
        print_error(
            "No active repo configured",
            hint="Run 'dark-factory onboard' first or set GITHUB_REPO env var.",
        )
        raise SystemExit(1)

    # Web apps need Docker; console apps run natively
    ws_cfg = _resolve_workspace_config(repo)
    app_type = ws_cfg.get("app_type", "console")
    if app_type == "web" and not shutil.which("docker"):
        print_error(
            "Crucible requires Docker for web apps but 'docker' was not found",
            hint="Install Docker: https://docs.docker.com/get-docker/",
        )
        raise SystemExit(1)

    cprint(f"Crucible: acquiring workspace for PR #{pr_number}...", "info")
    try:
        workspace = acquire_workspace(repo, pr_number)
    except Exception as exc:
        print_error(f"Failed to acquire workspace: {exc}")
        raise SystemExit(1) from None

    ctx = _build_crucible_context(repo, workspace.path, pr_number)
    cprint(f"Crucible: validating PR #{pr_number} ({app_type} app)...", "info")
    engine = FactoryPipelineEngine()
    pipeline_result = asyncio.run(engine.run_pipeline("crucible", ctx))
    outcome = _extract_crucible_verdict(pipeline_result)
    cprint(f"Crucible verdict: {outcome.verdict}", "success" if outcome.verdict == "go" else "error")
    cprint(f"  duration={outcome.duration_s:.1f}s", "muted")
    if outcome.error:
        print_error(outcome.error)
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


def _resolve_workspace_config(repo: str) -> dict:
    """Return the workspace_config dict for *repo* from config.json.

    Contains analysis results (language, framework, test_cmd, app_type),
    crucible config (test_repo), and ouroboros config.
    Returns empty dict if not found.
    """
    from dark_factory.core.config_manager import load_config  # noqa: PLC0415

    cfg = load_config()
    for r in cfg.data.get("repos", []):
        if isinstance(r, dict) and r.get("name") == repo:
            ws_cfg = r.get("workspace_config", {})
            return ws_cfg if isinstance(ws_cfg, dict) else {}
    return {}


def _run_subsystem(key: str) -> None:
    """Launch the subsystem selected from the interactive TUI menu.

    Menu keys match :data:`~dark_factory.modes.interactive.MENU_ITEMS`:
    1=Dark Forge, 2=Crucible, 3=Ouroboros, 4=Foundry, 5=Settings.
    """
    if key == "1":
        # Dark Forge — run one dispatch cycle through the full pipeline
        _run_forge_interactive()
    elif key == "2":
        # Crucible — prompt for PR number and validate
        _run_crucible_interactive()
    elif key == "3":
        # Ouroboros — invoke → gather deps → execute (same as other pillars)
        _run_ouroboros_interactive()
    elif key == "4":
        # Foundry — workspace manager TUI (loops until Escape)
        _run_foundry_interactive()
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


def _run_foundry_interactive() -> None:
    """Foundry workspace manager — loops until the user presses Escape.

    When the user presses Enter on a workspace row, opens the per-workspace
    config editor.  When ``"add"`` is returned, launches onboarding.
    ``None`` (Escape/quit) exits back to the main menu.
    """
    from dark_factory.modes.foundry import run_foundry_tui, run_workspace_config_tui  # noqa: PLC0415

    while True:
        result = run_foundry_tui()
        if result is None:
            break
        if result == "add":
            from dark_factory.setup.orchestrator import run_onboarding  # noqa: PLC0415

            run_onboarding()
        else:
            # Open per-workspace config editor
            run_workspace_config_tui(result)


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

    Follows the Invoke → Gather → Execute pattern:
      1. Resolve active repo + workspace config
      2. Prompt for PR number
      3. acquire_workspace() — clone/pull, bootstrap agents/pipelines
      4. engine.run_pipeline("crucible", full context dict)
      5. Print verdict
    """
    import asyncio  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    from dark_factory.ui.cli_colors import cprint, print_error  # noqa: PLC0415

    cprint("")
    try:
        raw = input("  Enter PR number to validate: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not raw.isdigit() or int(raw) <= 0:
        cprint("  Invalid PR number.", "warning")
        input("\n  Press Enter to return to menu...")
        return

    pr_number = int(raw)
    repo = _resolve_active_repo()
    if not repo:
        print_error("No active repo configured", hint="Run 'dark-factory onboard' first")
        input("\n  Press Enter to return to menu...")
        return

    # Web apps need Docker; console apps run natively
    ws_cfg = _resolve_workspace_config(repo)
    app_type = ws_cfg.get("app_type", "console")
    if app_type == "web" and not shutil.which("docker"):
        print_error("Crucible requires Docker for web apps but 'docker' was not found",
                    hint="Install Docker: https://docs.docker.com/get-docker/")
        input("\n  Press Enter to return to menu...")
        return

    from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415
    from dark_factory.workspace.manager import acquire_workspace  # noqa: PLC0415

    cprint(f"  Crucible: acquiring workspace for PR #{pr_number}...", "info")
    try:
        workspace = acquire_workspace(repo, pr_number)
    except Exception as exc:  # noqa: BLE001
        print_error(f"Failed to acquire workspace: {exc}")
        input("\n  Press Enter to return to menu...")
        return

    ctx = _build_crucible_context(repo, workspace.path, pr_number)
    cprint(f"  Crucible: validating PR #{pr_number} ({app_type} app)...", "info")
    engine = FactoryPipelineEngine()
    pipeline_result = asyncio.run(engine.run_pipeline("crucible", ctx))
    outcome = _extract_crucible_verdict(pipeline_result)

    if outcome.verdict == "go":
        cprint(f"  Verdict: {outcome.verdict}", "success")
    else:
        cprint(f"  Verdict: {outcome.verdict}", "error")
    cprint(f"  duration={outcome.duration_s:.1f}s", "muted")
    if outcome.error:
        print_error(outcome.error)
    input("\n  Press Enter to return to menu...")


def _resolve_ouroboros_repo() -> str:
    """Return the Ouroboros target repo from config, or empty if disabled."""
    from dark_factory.core.config_manager import load_config  # noqa: PLC0415

    cfg = load_config()
    for r in cfg.data.get("repos", []):
        if isinstance(r, dict) and r.get("active"):
            ws_cfg = r.get("workspace_config", {})
            if isinstance(ws_cfg, dict):
                ouro = ws_cfg.get("ouroboros", {})
                if isinstance(ouro, dict):
                    return str(ouro.get("repo", ""))
    return ""


def _run_ouroboros_interactive() -> None:
    """Run Ouroboros self-improvement pipeline interactively.

    Follows the Invoke → Gather Dependencies → Execute pattern:
      1. Resolve Ouroboros target repo (Dark Factory upstream or user's fork)
      2. acquire_workspace() — clone/pull the factory repo
      3. engine.run_pipeline("ouroboros", {workspace_root, upstream_repo, trigger})
      4. Print result

    Ouroboros operates on Dark Factory's own code, NOT the user's project.
    The target repo is configured during onboarding (phase 10).
    """
    import asyncio  # noqa: PLC0415
    import time  # noqa: PLC0415

    from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415
    from dark_factory.ui.cli_colors import cprint, print_error  # noqa: PLC0415
    from dark_factory.workspace.manager import acquire_workspace  # noqa: PLC0415

    ouro_repo = _resolve_ouroboros_repo()
    if not ouro_repo:
        cprint("\n  Ouroboros is not configured.", "warning")
        cprint("  Run 'dark-factory onboard' to enable it.", "muted")
        input("\n  Press Enter to return to menu...")
        return

    cprint(f"\n  Ouroboros: targeting {ouro_repo}", "info")
    cprint("  Acquiring workspace...", "muted")
    try:
        workspace = acquire_workspace(ouro_repo, "ouroboros")
    except Exception as exc:  # noqa: BLE001
        print_error(f"Failed to acquire workspace: {exc}")
        input("\n  Press Enter to return to menu...")
        return

    cprint("  Running self-improvement pipeline...", "info")
    engine = FactoryPipelineEngine(on_event=_forge_event_printer)
    t0 = time.monotonic()
    try:
        asyncio.run(engine.run_pipeline("ouroboros", {
            "workspace_root": workspace.path,
            "upstream_repo": f"https://github.com/{ouro_repo}.git",
            "trigger": "manual",
        }))
        passed = True
    except Exception as exc:  # noqa: BLE001
        print_error(f"Pipeline error: {exc}")
        passed = False
    elapsed = time.monotonic() - t0

    if passed:
        cprint(f"  Ouroboros PASSED ({elapsed:.1f}s)", "success")
    else:
        cprint(f"  Ouroboros FAILED ({elapsed:.1f}s)", "error")
    input("\n  Press Enter to return to menu...")


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
                repo = _resolve_active_repo()
                selection = run_interactive_tui(active_repo=repo)
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
