"""Command handler implementations for the dark-factory CLI.

Each handler encapsulates the core logic for one CLI command.
They are called by both the Click commands (backward compatibility)
and the argparse-based dispatch path.
"""

from __future__ import annotations

import sys


def run_doctor(*, modules: bool, debug_modules: bool, deps: bool = False, lint: bool = False) -> None:
    """Execute the ``doctor`` command logic.

    Parameters
    ----------
    modules:
        Validate the module manifest.
    debug_modules:
        Show which modules loaded and when.
    deps:
        Validate the module dependency graph.
    lint:
        Run file size compliance check.
    """
    if lint:
        from dark_factory.tools import lint_file_sizes

        lint_result = lint_file_sizes.validate()
        sys.stdout.write(lint_file_sizes.format_report(lint_result) + "\n")
        if not lint_result.passed:
            raise SystemExit(1)
    elif deps:
        from dark_factory.tools import dependency_graph as depgraph

        dep_result = depgraph.validate()
        sys.stdout.write(depgraph.format_report(dep_result) + "\n")
        if not dep_result.passed:
            raise SystemExit(1)
    elif modules:
        from dark_factory.core.module_loader import (
            format_validation_report,
            validate_manifest,
        )

        passed, issues, count = validate_manifest()
        sys.stdout.write(format_validation_report(passed, issues, count) + "\n")
        if not passed:
            raise SystemExit(1)
    elif debug_modules:
        from dark_factory.core.module_loader import format_debug_report

        sys.stdout.write(format_debug_report() + "\n")
    else:
        sys.stdout.write("dark-factory doctor: all checks passed.\n")


def run_ingest(
    *,
    prd_path: str,
    repo: str = "",
    validate_only: bool = False,
    force: bool = False,
    auto_split: bool = False,
) -> None:
    """Execute the ``ingest`` command logic.

    Parameters
    ----------
    prd_path:
        Path to the PRD file (JSON or Markdown).
    repo:
        GitHub repo (owner/name) for issue creation.
    validate_only:
        If True, validate without creating issues.
    force:
        If True, skip confirmation prompts.
    auto_split:
        If True, auto-split oversized stories.
    """
    from pathlib import Path  # noqa: PLC0415

    from dark_factory.specs.prd_ingest import ingest_prd  # noqa: PLC0415

    result = ingest_prd(
        path=Path(prd_path),
        repo=repo,
        validate_only=validate_only,
        force=force,
        auto_split=auto_split,
    )
    if result.errors:
        raise SystemExit(1)


def run_onboard(*, self_onboard: bool) -> None:
    """Execute the ``onboard`` command logic.

    Parameters
    ----------
    self_onboard:
        If True, run the factory self-onboarding flow.
    """
    if not self_onboard:
        sys.stdout.write("Usage: dark-factory onboard --self\n")
        return

    from dark_factory.setup.self_onboard import run_onboard_self

    result = run_onboard_self()
    sys.stdout.write("\n--- Onboarding Summary ---\n")
    for step in result.steps:
        sys.stdout.write(f"  {step}\n")
    if result.passed:
        sys.stdout.write("\nonboard --self: PASS\n")
    else:
        sys.stdout.write("\nonboard --self: FAIL\n")
        raise SystemExit(1)


def run_selftest() -> None:
    """Execute the ``selftest`` command logic.

    Runs all built-in validators (module manifest + dependency graph)
    and reports a combined pass/fail status.
    """
    from dark_factory.core.module_loader import (
        format_validation_report,
        validate_manifest,
    )
    from dark_factory.tools import dependency_graph as depgraph

    all_passed = True

    # 1) Module manifest validation
    passed, issues, count = validate_manifest()
    sys.stdout.write(format_validation_report(passed, issues, count) + "\n\n")
    if not passed:
        all_passed = False

    # 2) Dependency graph validation
    graph_result = depgraph.validate()
    sys.stdout.write(depgraph.format_report(graph_result) + "\n")
    if not graph_result.passed:
        all_passed = False

    if all_passed:
        sys.stdout.write("\nselftest: PASS\n")
    else:
        sys.stdout.write("\nselftest: FAIL\n")
        raise SystemExit(1)


def run_status(*, epics: bool) -> None:
    """Execute the ``status`` command logic.

    Parameters
    ----------
    epics:
        Show epic-level progress.
    """
    import os  # noqa: PLC0415

    from dark_factory.ui.status_reporter import (
        show_epic_status,
        show_status,
    )

    if epics:
        repo = os.environ.get("DARK_FACTORY_REPO", os.environ.get("REPO", ""))
        if repo:
            from dark_factory.pipeline.epic_milestones import (  # noqa: PLC0415
                epic_status_summary,
                format_epic_summary,
            )

            statuses = epic_status_summary(repo)
            sys.stdout.write(format_epic_summary(statuses) + "\n")
        else:
            sys.stdout.write(show_epic_status() + "\n")
    else:
        sys.stdout.write(show_status() + "\n")


def run_gates(*, run_all: bool, list_gates: bool, run_name: str) -> None:
    """Execute the ``gates`` command logic.

    Parameters
    ----------
    run_all:
        Run every discovered gate in sequence.
    list_gates:
        List all discovered gates with their check counts.
    run_name:
        Run a single gate by name.
    """
    from dark_factory.gates import (
        discover_gates,
        format_gate_list,
        format_unified_report,
        run_all_gates,
        run_gate_by_name,
    )

    if list_gates:
        gates = discover_gates()
        sys.stdout.write(format_gate_list(gates) + "\n")
    elif run_name:
        try:
            report = run_gate_by_name(run_name)
        except KeyError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            raise SystemExit(1) from None
        from dark_factory.gates import UnifiedReport

        unified = UnifiedReport(gate_reports=(report,))
        sys.stdout.write(format_unified_report(unified) + "\n")
        if not report.passed:
            raise SystemExit(1)
    elif run_all:
        unified = run_all_gates()
        sys.stdout.write(format_unified_report(unified) + "\n")
        if not unified.overall_passed:
            raise SystemExit(1)
    else:
        sys.stdout.write("Usage: dark-factory gates [--run-all | --list | --run <name>]\n")


def run_dashboard() -> None:
    """Launch the Textual TUI dashboard."""
    from dark_factory.ui.dashboard import DashboardApp

    app = DashboardApp()
    app.run()


def run_smoke_test(*, title: str) -> None:
    """Execute the ``smoke-test`` command logic.

    Parameters
    ----------
    title:
        Story title for the smoke test.
    """
    from dark_factory.pipeline.runner import StoryContext, run_pipeline

    story = StoryContext(
        title=title,
        description="Smoke test: verify the pipeline processes a Python story.",
        acceptance_criteria=("Pipeline completes all six stages.",),
        changed_files=("factory/pipeline/runner.py",),
    )
    result = run_pipeline(story)

    from dark_factory.ui.cli_colors import cprint, print_stage_result  # noqa: PLC0415

    for stage_result in result.stages:
        state = "passed" if stage_result.passed else "failed"
        print_stage_result(stage_result.stage.value, state, stage_result.detail)

    if result.passed:
        cprint("\nsmoke-test: PASS", "success")
    else:
        cprint("\nsmoke-test: FAIL", "error")
        raise SystemExit(1)


# ── Config helpers ───────────────────────────────────────────────


def _cfg_apply(data: dict, key: str, val: object) -> None:  # type: ignore[type-arg]
    """Set a value in a nested dict using a dotted key."""
    parts = key.split(".")
    cur = data
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = val


def _cfg_get(data: dict, key: str) -> object:  # type: ignore[type-arg]
    """Get a value from a nested dict using a dotted key."""
    cur: object = data
    for p in key.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _cfg_coerce(raw: str) -> int | float | bool | str:
    """Best-effort coercion of a CLI string to a typed value."""
    low = raw.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _cfg_flatten(data: dict, prefix: str = "") -> list[str]:  # type: ignore[type-arg]
    """Flatten a nested dict to ``dotted.key = value`` lines."""
    lines: list[str] = []
    for k, v in sorted(data.items()):
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            lines.extend(_cfg_flatten(v, full))
        else:
            lines.append(f"{full} = {v!r}")
    return lines


def run_workspace(*, action: str, name: str = "") -> None:
    """Execute the ``workspace`` command logic.

    Parameters
    ----------
    action:
        One of ``"list"``, ``"clean"``, ``"purge"``, or ``"stats"``.
    name:
        Workspace name (only used with ``action="clean"``).
    """
    import time  # noqa: PLC0415

    from dark_factory.workspace.manager import (  # noqa: PLC0415
        clean_all_workspaces,
        clean_workspace,
        list_workspaces,
    )

    if action == "list":
        workspaces = list_workspaces()
        if not workspaces:
            sys.stdout.write("No workspaces found.\n")
            return
        for ws in workspaces:
            wt = " [worktree]" if ws.is_worktree else ""
            sys.stdout.write(f"  {ws.name}  {ws.path}  ({ws.branch}){wt}\n")
    elif action == "clean":
        if not name:
            sys.stderr.write("Error: workspace name required.\n")
            raise SystemExit(2)
        result = clean_workspace(name)
        if result.success:
            sys.stdout.write(f"Cleaned workspace '{name}'.\n")
        else:
            sys.stderr.write(f"Error: {result.message}\n")
            raise SystemExit(1)
    elif action == "purge":
        count = clean_all_workspaces()
        sys.stdout.write(f"Purged {count} workspace(s).\n")
    elif action == "stats":
        workspaces = list_workspaces()
        now = time.time()
        total = len(workspaces)
        worktrees = sum(1 for ws in workspaces if ws.is_worktree)
        clones = total - worktrees
        oldest_age = max((now - ws.created_at for ws in workspaces), default=0.0)
        sys.stdout.write(f"Total workspaces: {total}\n")
        sys.stdout.write(f"  Clones:    {clones}\n")
        sys.stdout.write(f"  Worktrees: {worktrees}\n")
        if total:
            sys.stdout.write(f"  Oldest:    {oldest_age / 3600:.1f}h ago\n")
    else:
        sys.stdout.write("Usage: dark-factory workspace [list|clean|purge|stats]\n")


def run_config(*, action: str, key: str = "", value: str = "") -> None:
    """Execute the ``config`` command logic.

    Parameters
    ----------
    action:
        One of ``"set"``, ``"get"``, or ``"list"``.
    key:
        Dotted config key (e.g. ``"analysis.language"``).
    value:
        Value to set (only used with ``action="set"``).
    """
    import json  # noqa: PLC0415

    from dark_factory.core.config_manager import resolve_config_path  # noqa: PLC0415

    config_path = resolve_config_path()

    # Read existing JSON (empty dict if file missing)
    if config_path.is_file():
        raw = config_path.read_text(encoding="utf-8")
        data: dict = json.loads(raw) if raw.strip() else {}  # type: ignore[type-arg]
    else:
        data = {}

    if action == "set":
        _cfg_apply(data, key, _cfg_coerce(value))
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    elif action == "get":
        val = _cfg_get(data, key)
        if val is None:
            sys.stderr.write(f"Key not found: {key}\n")
            raise SystemExit(1)
        sys.stdout.write(f"{val}\n")
    else:  # list
        if not data:
            sys.stdout.write("(no config values set)\n")
            return
        for line in _cfg_flatten(data):
            sys.stdout.write(f"{line}\n")
