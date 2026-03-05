"""Onboarding orchestrator — thin sequencer over DOT pipelines.

Architecture
~~~~~~~~~~~~
This module is the **entry point** for ``dark-factory onboard``.  It sequences
10 numbered phases displayed to the user and returns 0 (success) or 1 (failure).

**Key design rule: this file is WIRING, not LOGIC.**
All substantive work lives in either:
  - DOT pipeline files under ``pipelines/`` (executed by FactoryPipelineEngine)
  - Dedicated setup modules under ``setup/``

Do NOT add heuristics, pattern tables, or detection logic here.  If a phase
needs smarts, create a DOT pipeline and call it via ``_run_pipeline()``.

Phase flow
~~~~~~~~~~
::

    [1/10]  Platform Detection       detect_platform() + check_dependencies()
    [2/10]  Dependencies             print found/missing via print_stage_result()
    [3/10]  Claude Model             detect_claude_model()
    [4/10]  GitHub Authentication    auto_connect_github() / connect_github()
    [5/10]  Repository Connection    GITHUB_REPO env or interactive prompt
    [6/10]  Workspace                create_workspace() — permanent clone
    [7/10]  Project Analysis         ← DOT pipeline (project_analysis.dot)
    [8/10]  Environment Setup        ← DOT pipeline (workspace_bootstrap.dot)
    [9/10]  Configuration            init_config() + add_repo_to_config()
    [10/10] GitHub Provisioning      provision_github() + crucible + app_type

Pipeline integration
~~~~~~~~~~~~~~~~~~~~
- ``_run_project_analysis(ws_path)`` — runs ``project_analysis`` pipeline,
  parses JSON output into ``AnalysisResult`` (from ``project_analyzer.py``).
- ``_bootstrap_workspace_env(ws_path, analysis)`` — runs ``workspace_bootstrap``
  pipeline, parses JSON output into ``BootstrapResult`` (from ``dep_installer.py``).
  Accepts ``_run_pipeline`` kwarg for DI in tests.

Both pipeline calls are wrapped in ``spinner()`` for visual feedback.

Display
~~~~~~~
All user-facing output uses ``ui.cli_colors`` (``cprint``, ``phase_header``,
``print_stage_result``, ``completion_panel``, ``print_error``, ``spinner``).
Raw ``sys.stdout.write`` is only used in ``_prompt_repo`` / ``_detect_existing_repo``
for interactive input prompts.

Diagnostic log
~~~~~~~~~~~~~~
Every phase writes markers to ``onboarding.log`` via the ``_Log`` class.
The log is rotated to keep at most 3 runs.  This log is for post-mortem
debugging, NOT for user display.
"""
from __future__ import annotations

import os
import re
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

_LOG_FILE = "onboarding.log"
_MAX_RUNS = 3
_SEP = "=" * 72
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _log_path(start: Path | None = None) -> Path:
    from dark_factory.core.config_manager import resolve_config_dir  # noqa: PLC0415
    log_dir = resolve_config_dir(start) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / _LOG_FILE


def _rotate(path: Path) -> None:
    """Trim log so at most _MAX_RUNS runs remain after the next append."""
    if not path.is_file():
        return
    parts = path.read_text(encoding="utf-8", errors="replace").split(_SEP)
    non_empty = [p for p in parts if p.strip()]
    if len(non_empty) >= _MAX_RUNS:
        kept = non_empty[-(_MAX_RUNS - 1) :]
        path.write_text(_SEP.join(["", *kept]), encoding="utf-8")


class _Log:
    """Diagnostic logger writing phase markers to onboarding.log."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._buf: list[str] = []

    def start(self) -> None:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._buf = [_SEP, f"\nOnboarding run: {ts}\n"]

    def phase_start(self, name: str) -> None:
        self._buf.append(f"[{self._ts()}] START {name}")

    def phase_end(self, name: str, ok: bool) -> None:
        self._buf.append(f"[{self._ts()}] END   {name} -- {'OK' if ok else 'FAIL'}")

    def info(self, msg: str) -> None:
        self._buf.append(f"  {msg}")

    def error(self, msg: str, *, line: int = 0, command: str = "", exit_code: int = 0) -> None:
        self._buf.append(f"  ERROR: {msg}")
        if line:
            self._buf.append(f"    line={line}")
        if command:
            self._buf.append(f"    command={command}")
        if exit_code:
            self._buf.append(f"    exit_code={exit_code}")

    def flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write("\n".join(self._buf) + "\n")
        self._buf.clear()

    @staticmethod
    def _ts() -> str:
        return datetime.now(UTC).strftime("%H:%M:%S")


@contextmanager
def _phase(log: _Log, name: str) -> Iterator[None]:
    """Wrap a phase with start/end markers and error capture."""
    log.phase_start(name)
    try:
        yield
        log.phase_end(name, True)
    except Exception as exc:  # noqa: BLE001
        tb = traceback.extract_tb(exc.__traceback__)
        last = tb[-1] if tb else None
        rc = getattr(exc, "returncode", 0)
        log.error(
            str(exc), line=(last.lineno or 0) if last else 0,
            command=last.name if last else "",
            exit_code=int(rc) if isinstance(rc, (int, float)) else 0,
        )
        log.phase_end(name, False)
        raise


def _prompt_repo(w, dl: _Log) -> str:  # type: ignore[type-arg]
    """Prompt for owner/repo with retry loop and validation."""
    for attempt in range(3):
        try:
            raw = input("  Enter GitHub repo (owner/repo): ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
        if not raw:
            w("  No input provided.\n")
            continue
        if not _REPO_RE.match(raw):
            w(f"  Invalid format: '{raw}' -- expected owner/repo\n")
            dl.info(f"repo attempt {attempt + 1}: invalid format: {raw!r}")
            continue
        return raw
    w("  Too many invalid attempts.\n")
    return ""


def _detect_existing_repo(w, dl: _Log, start: Path | None) -> str:  # type: ignore[type-arg]
    """Check config for an existing repo and offer to reuse it."""
    import json  # noqa: PLC0415
    from dark_factory.core.config_manager import resolve_config_path  # noqa: PLC0415

    config_path = resolve_config_path(start)
    if not config_path.is_file():
        return ""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    repos = data.get("repos", [])
    if not isinstance(repos, list):
        return ""
    active = [r for r in repos if isinstance(r, dict) and r.get("active")
              and isinstance(r.get("name"), str) and "/" in r["name"] and ":\\" not in r["name"]]
    if not active:
        return ""
    name = active[0]["name"]
    w(f"\n  Previously configured repo: {name}\n")
    try:
        choice = input("  Use this repo? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = "y"
    if choice in ("", "y", "yes"):
        dl.info(f"reusing existing repo: {name}")
        return name
    return ""


def _run_project_analysis(clone_dir: str) -> AnalysisResult:
    """Run the project_analysis DOT pipeline and parse the result."""
    import asyncio  # noqa: PLC0415
    import json  # noqa: PLC0415

    from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415
    from dark_factory.setup.project_analyzer import AnalysisResult  # noqa: PLC0415

    engine = FactoryPipelineEngine()
    result = asyncio.run(engine.run_pipeline("project_analysis", {
        "workspace": clone_dir,
    }))

    raw = result.context.get("codergen.analyze.output", "")
    if not raw:
        return AnalysisResult(description="Pipeline produced no output")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return AnalysisResult(description="Pipeline output was not valid JSON")

    if not isinstance(data, dict):
        return AnalysisResult(description="Pipeline output was not a JSON object")

    # Convert list fields to tuples for the frozen dataclass
    for key in ("required_tools", "source_dirs", "test_dirs", "aws_services"):
        val = data.get(key)
        if isinstance(val, list):
            data[key] = tuple(val)

    # Only pass keys that AnalysisResult accepts
    import dataclasses  # noqa: PLC0415
    valid_keys = {f.name for f in dataclasses.fields(AnalysisResult)}
    filtered = {k: v for k, v in data.items() if k in valid_keys}

    return AnalysisResult(**filtered)


def _bootstrap_workspace_env(
    workspace: str,
    analysis: object,
    *,
    _run_pipeline: object = None,
) -> BootstrapResult:
    """Run workspace_bootstrap pipeline to set up a scoped dev environment.

    Parameters
    ----------
    _run_pipeline:
        Callable ``(name, context) -> result`` for DI in tests.
        Defaults to ``asyncio.run(engine.run_pipeline(...))``.
    """
    import json  # noqa: PLC0415

    from dark_factory.setup.dep_installer import BootstrapResult  # noqa: PLC0415

    if _run_pipeline is None:
        import asyncio  # noqa: PLC0415

        from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415

        engine = FactoryPipelineEngine()

        def _run_pipeline(name: str, ctx: dict) -> object:  # type: ignore[misc]
            return asyncio.run(engine.run_pipeline(name, ctx))

    result = _run_pipeline("workspace_bootstrap", {  # type: ignore[operator]
        "workspace": workspace,
        "language": getattr(analysis, "language", ""),
        "framework": getattr(analysis, "framework", ""),
        "test_cmd": getattr(analysis, "test_cmd", ""),
    })

    raw = result.context.get("codergen.bootstrap.output", "")  # type: ignore[union-attr]
    if not raw:
        return BootstrapResult(errors=("Pipeline produced no output",))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return BootstrapResult(errors=("Pipeline output was not valid JSON",))

    if not isinstance(data, dict):
        return BootstrapResult(errors=("Pipeline output was not a JSON object",))

    errors = data.get("errors", [])
    if isinstance(errors, list):
        errors = tuple(str(e) for e in errors)
    else:
        errors = ()

    return BootstrapResult(
        runtime_ok=bool(data.get("runtime_ok", False)),
        env_created=bool(data.get("env_created", False)),
        deps_installed=bool(data.get("deps_installed", False)),
        env_path=str(data.get("env_path", "")),
        errors=errors,
    )


def run_onboarding(auto_mode: bool = False, *, start: Path | None = None) -> int:  # noqa: PLR0912, PLR0915
    """Sequence all onboarding phases. Returns 0 on success, 1 on failure."""
    from dark_factory.integrations.shell import gh as gh_cmd  # noqa: PLC0415
    from dark_factory.setup.claude_detect import (  # noqa: PLC0415
        detect_claude_model,
        prompt_claude_model,
        save_claude_model,
    )
    from dark_factory.setup.config_init import (  # noqa: PLC0415
        add_repo_to_config,
        init_config,
    )
    from dark_factory.setup.github_auth import auto_connect_github, connect_github  # noqa: PLC0415
    from dark_factory.setup.github_provision import provision_github  # noqa: PLC0415
    from dark_factory.setup.platform import check_dependencies, detect_platform  # noqa: PLC0415
    from dark_factory.setup.project_analyzer import (  # noqa: PLC0415
        confirm_or_override_analysis,
        display_analysis_results,
    )
    from dark_factory.ui.cli_colors import (  # noqa: PLC0415
        completion_panel,
        cprint,
        phase_header,
        print_error,
        print_stage_result,
        spinner,
    )
    from dark_factory.ui.theme import FULL_HEADER_BANNER  # noqa: PLC0415

    _TOTAL = 10
    lp = _log_path(start)
    _rotate(lp)
    dl = _Log(lp)
    dl.start()
    w = sys.stdout.write  # kept for interactive prompts in _prompt_repo/_detect_existing_repo

    try:
        # ── Banner ───────────────────────────────────────────────
        cprint(FULL_HEADER_BANNER)
        cprint("")

        # ── [1/10] Platform Detection ────────────────────────────
        cprint(phase_header(1, _TOTAL, "Platform Detection"), end="")
        with _phase(dl, "platform-detect"):
            plat = detect_platform()
        dl.info(f"os={plat.os} arch={plat.arch} shell={plat.shell}")
        cprint(f"  {plat.os}/{plat.arch} ({plat.shell})", "info")

        # ── [2/10] Dependencies ──────────────────────────────────
        cprint(phase_header(2, _TOTAL, "Dependencies"), end="")
        with _phase(dl, "deps-check"):
            deps = check_dependencies(plat)
        missing = [d.name for d in deps if not d.found]
        for d in deps:
            print_stage_result(d.name, "passed" if d.found else "failed")
        if missing:
            dl.info(f"missing: {', '.join(missing)}")

        # ── [3/10] Claude Model ──────────────────────────────────
        cprint(phase_header(3, _TOTAL, "Claude Model"), end="")
        with _phase(dl, "claude-detect"):
            model = detect_claude_model()
            if not model and not auto_mode:
                model = prompt_claude_model()
            if model:
                save_claude_model(model)
        dl.info(f"model={model or 'none'}")
        if model:
            cprint(f"  {model}", "success")
        else:
            cprint("  not configured", "muted")

        # ── [4/10] GitHub Authentication ─────────────────────────
        cprint(phase_header(4, _TOTAL, "GitHub Authentication"), end="")
        with _phase(dl, "github-auth"):
            gh_ok = auto_connect_github() if auto_mode else connect_github()
        if not gh_ok:
            dl.info("github auth failed -- continuing")
            cprint("  not authenticated (continuing)", "warning")
        else:
            cprint("  authenticated", "success")

        # ── [5/10] Repository Connection ─────────────────────────
        cprint(phase_header(5, _TOTAL, "Repository Connection"), end="")
        with _phase(dl, "repo-connect"):
            if auto_mode:
                repo = os.environ.get("GITHUB_REPO", "")
                if not repo:
                    dl.error("GITHUB_REPO not set in auto mode")
                    print_error("No GITHUB_REPO set", hint="export GITHUB_REPO=owner/repo")
                    return 1
            else:
                repo = _detect_existing_repo(w, dl, start)
                if not repo:
                    repo = _prompt_repo(w, dl)
                if not repo:
                    dl.error("No repo provided")
                    print_error("Onboarding cancelled — no repo provided")
                    return 1

            check = gh_cmd(["repo", "view", repo], timeout=30)
            if check.returncode != 0:
                dl.error(f"Cannot access repo: {repo}")
                print_error(f"Cannot access repo: {repo}", hint="Check access and gh auth status")
                return 1
        dl.info(f"repo={repo}")
        cprint(f"  {repo}", "success")

        os.environ["GITHUB_REPO"] = repo

        # ── [6/10] Workspace ─────────────────────────────────────
        cprint(phase_header(6, _TOTAL, "Workspace"), end="")
        with _phase(dl, "acquire-workspace"):
            from dark_factory.workspace.manager import create_workspace  # noqa: PLC0415

            ws_result = create_workspace(repo, f"https://github.com/{repo}.git")
            if not ws_result.success:
                dl.error(f"Workspace creation failed: {ws_result.message}")
                print_error(f"Failed to create workspace for {repo}")
                return 1
            ws_path = ws_result.workspace.path  # type: ignore[union-attr]
        dl.info(f"workspace={ws_path}")
        cprint(f"  {ws_path}", "success")

        # ── [7/10] Project Analysis ──────────────────────────────
        cprint(phase_header(7, _TOTAL, "Project Analysis"), end="")
        with _phase(dl, "analyze"):
            with spinner("Analyzing project..."):
                analysis = _run_project_analysis(ws_path)
            display_analysis_results(analysis)
            if not auto_mode:
                analysis = confirm_or_override_analysis(analysis)
        dl.info(f"lang={analysis.language} fw={analysis.framework} app_type={analysis.detected_app_type}")

        app_type = analysis.detected_app_type

        # ── [8/10] Environment Setup ─────────────────────────────
        cprint(phase_header(8, _TOTAL, "Environment Setup"), end="")
        with _phase(dl, "workspace-bootstrap"):
            with spinner("Bootstrapping workspace environment..."):
                bootstrap = _bootstrap_workspace_env(ws_path, analysis)
        if bootstrap.success:
            cprint(f"  Environment ready: {bootstrap.env_path or 'default'}", "success")
        else:
            for err in bootstrap.errors:
                cprint(f"  ! {err}", "error")
            if not bootstrap.runtime_ok:
                cprint("  Runtime not found — install it and re-run onboarding.", "warning")
        dl.info(f"bootstrap: runtime={bootstrap.runtime_ok} env={bootstrap.env_created} deps={bootstrap.deps_installed}")

        # ── [9/10] Configuration ─────────────────────────────────
        cprint(phase_header(9, _TOTAL, "Configuration"), end="")
        with _phase(dl, "config-init"):
            init_config(start=start)
            add_repo_to_config(repo, app_type, analysis, start=start)
        cprint("  Config initialized", "success")

        # ── [10/10] GitHub Provisioning ──────────────────────────
        cprint(phase_header(10, _TOTAL, "GitHub Provisioning"), end="")
        with _phase(dl, "github-provision"):
            prov = provision_github(repo)
        label_count = prov.get("labels", 0)
        dl.info(f"provision: labels={label_count} ci={prov.get('ci_workflow')} "
                f"template={prov.get('issue_template')} protection={prov.get('branch_protection')}")

        with _phase(dl, "crucible-repo"):
            want_crucible = False
            if auto_mode:
                want_crucible = True
            else:
                cprint("\n  Crucible uses a companion test repo for end-to-end validation.", "muted")
                try:
                    choice = input("  Set up Crucible test repo now? [Y/n]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "n"
                want_crucible = choice in ("", "y", "yes")

            if want_crucible:
                from dark_factory.crucible.repo_provision import provision_crucible_repo  # noqa: PLC0415
                from dark_factory.core.config_manager import load_config as _load_cfg, save_config  # noqa: PLC0415

                global_cfg = _load_cfg(start)
                cr_result = provision_crucible_repo(repo, global_cfg.data)
                if cr_result.error:
                    dl.info(f"crucible-repo: {cr_result.error} (non-fatal)")
                    cprint(f"  Crucible repo: {cr_result.error}", "warning")
                else:
                    dl.info(f"crucible-repo: {cr_result.crucible_repo} created={cr_result.created}")
                    cprint(f"  Crucible repo: {cr_result.crucible_repo}", "success")
                    for entry in global_cfg.data.get("repos", []):
                        if isinstance(entry, dict) and entry.get("name") == repo:
                            ws_cfg = entry.setdefault("workspace_config", {})
                            ws_cfg.setdefault("crucible", {})["test_repo"] = cr_result.crucible_repo
                            break
                    save_config(global_cfg)
            else:
                dl.info("crucible-repo: skipped by user")
                cprint("  Crucible repo: skipped", "muted")

        with _phase(dl, "ouroboros-config"):
            ouroboros_repo = ""
            if auto_mode:
                ouroboros_repo = os.environ.get("OUROBOROS_REPO", "")
            else:
                cprint("")
                cprint("  Ouroboros can improve Dark Factory itself — fix bugs,", "muted")
                cprint("  add features, and refine code autonomously.", "muted")
                cprint("")
                cprint("  [1] Contribute to Dark Factory (upstream)", "info")
                cprint("  [2] Point to your own fork", "info")
                cprint("  [3] Skip — disable Ouroboros", "muted")
                cprint("")
                try:
                    choice = input("  Enable Ouroboros? [1/2/3]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    choice = "3"
                if choice == "1":
                    ouroboros_repo = "pdickeyg/dark-factory"
                    cprint(f"  Ouroboros: {ouroboros_repo}", "success")
                elif choice == "2":
                    try:
                        ouroboros_repo = input("  Enter fork repo (owner/repo): ").strip()
                    except (EOFError, KeyboardInterrupt):
                        ouroboros_repo = ""
                    if ouroboros_repo and _REPO_RE.match(ouroboros_repo):
                        cprint(f"  Ouroboros: {ouroboros_repo}", "success")
                    else:
                        cprint("  Invalid repo — Ouroboros disabled", "warning")
                        ouroboros_repo = ""
                else:
                    cprint("  Ouroboros: disabled", "muted")

            if ouroboros_repo:
                from dark_factory.core.config_manager import load_config as _load_ouro_cfg, save_config as _save_ouro_cfg  # noqa: PLC0415
                ouro_cfg = _load_ouro_cfg(start)
                for entry in ouro_cfg.data.get("repos", []):
                    if isinstance(entry, dict) and entry.get("name") == repo:
                        ws_cfg = entry.setdefault("workspace_config", {})
                        ws_cfg["ouroboros"] = {"repo": ouroboros_repo}
                        break
                _save_ouro_cfg(ouro_cfg)
            dl.info(f"ouroboros: repo={ouroboros_repo or 'disabled'}")

        with _phase(dl, "app-type-bootstrap"):
            from dark_factory.strategies import resolve_app_type  # noqa: PLC0415
            cfg = resolve_app_type(app_type)
            dl.info(f"app-type-deps={', '.join(cfg.bootstrap_deps)}")

        # ── Completion ───────────────────────────────────────────
        dl.flush()
        cprint("")
        cprint(completion_panel(repo, app_type, label_count))
        return 0
    except Exception as exc:  # noqa: BLE001
        dl.flush()
        print_error(f"Onboarding failed: {exc}", hint=f"Diagnostic log: {lp}")
        return 1
