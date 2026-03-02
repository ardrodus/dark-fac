"""Onboarding orchestrator with rotating diagnostic log.

Sequences all onboarding phases: platform detection, dependency check,
Claude model, GitHub auth, repo connection (with retry), shallow clone,
project analysis, strategy selection, config init, dep install, Docker gen,
GitHub provisioning (labels, CI, branch protection), and GITHUB_REPO export.
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
        prompt_deployment_strategy,
    )
    from dark_factory.setup.dep_installer import install_project_deps  # noqa: PLC0415
    from dark_factory.setup.docker_gen import write_generated_files  # noqa: PLC0415
    from dark_factory.setup.github_auth import auto_connect_github, connect_github  # noqa: PLC0415
    from dark_factory.setup.github_provision import provision_github  # noqa: PLC0415
    from dark_factory.setup.platform import check_dependencies, detect_platform  # noqa: PLC0415
    from dark_factory.setup.project_analyzer import (  # noqa: PLC0415
        analyze_project,
        confirm_or_override_analysis,
        display_analysis_results,
    )

    lp = _log_path(start)
    _rotate(lp)
    dl = _Log(lp)
    dl.start()
    w = sys.stdout.write
    clone_dir: Path | None = None

    try:
        # ── Phase A: Platform & Dependencies ──────────────────────
        with _phase(dl, "platform-detect"):
            plat = detect_platform()
        dl.info(f"os={plat.os} arch={plat.arch} shell={plat.shell}")
        w(f"  Platform: {plat.os}/{plat.arch} ({plat.shell})\n")

        with _phase(dl, "deps-check"):
            deps = check_dependencies(plat)
        missing = [d.name for d in deps if not d.found]
        for d in deps:
            status = "found" if d.found else "MISSING"
            w(f"    {d.name}: {status}\n")
        if missing:
            dl.info(f"missing: {', '.join(missing)}")

        with _phase(dl, "claude-detect"):
            model = detect_claude_model()
            if not model and not auto_mode:
                model = prompt_claude_model()
            if model:
                save_claude_model(model)
        dl.info(f"model={model or 'none'}")
        w(f"  Claude model: {model or 'not configured'}\n")

        # ── Phase B: GitHub Auth ──────────────────────────────────
        with _phase(dl, "github-auth"):
            gh_ok = auto_connect_github() if auto_mode else connect_github()
        if not gh_ok:
            dl.info("github auth failed -- continuing")
            w("  GitHub: not authenticated (continuing)\n")
        else:
            w("  GitHub: authenticated\n")

        # ── Phase C: Repo Connection ──────────────────────────────
        with _phase(dl, "repo-connect"):
            if auto_mode:
                repo = os.environ.get("GITHUB_REPO", "")
                if not repo:
                    dl.error("GITHUB_REPO not set in auto mode")
                    w("  No GITHUB_REPO set\n")
                    return 1
            else:
                # Check for previously configured repo
                repo = _detect_existing_repo(w, dl, start)
                if not repo:
                    repo = _prompt_repo(w, dl)
                if not repo:
                    dl.error("No repo provided")
                    w("  Onboarding cancelled -- no repo provided.\n")
                    return 1

            # Validate access via gh CLI
            check = gh_cmd(["repo", "view", repo], timeout=30)
            if check.returncode != 0:
                dl.error(f"Cannot access repo: {repo}")
                w(f"  Cannot access repo: {repo}\n")
                w("  Make sure you have access and gh is authenticated.\n")
                return 1
        dl.info(f"repo={repo}")
        w(f"  Repository: {repo}\n")

        # Export for downstream use
        os.environ["GITHUB_REPO"] = repo

        # ── Phase C7: Clone & Analyze ─────────────────────────────
        with _phase(dl, "clone"):
            import tempfile  # noqa: PLC0415
            clone_dir = Path(tempfile.mkdtemp(prefix="df-onboard-"))
            clone_result = gh_cmd(
                ["repo", "clone", repo, str(clone_dir), "--", "--depth", "1"],
                timeout=120,
            )
            if clone_result.returncode != 0:
                dl.error(f"Clone failed: {clone_result.stderr}")
                w(f"  Failed to clone {repo}\n")
                return 1
        w(f"  Cloned {repo} (shallow)\n")

        with _phase(dl, "analyze"):
            analysis = analyze_project(str(clone_dir))
            display_analysis_results(analysis)
            if not auto_mode:
                analysis = confirm_or_override_analysis(analysis)
        dl.info(f"lang={analysis.language} fw={analysis.framework} strat={analysis.detected_strategy}")

        # ── Phase C9: Strategy Selection ──────────────────────────
        with _phase(dl, "strategy-select"):
            strategy = analysis.detected_strategy if auto_mode else prompt_deployment_strategy(analysis)
        dl.info(f"strategy={strategy}")
        w(f"  Strategy: {strategy}\n")

        # ── Phase C11: Install Project Deps ───────────────────────
        with _phase(dl, "install-deps"):
            dep_result = install_project_deps(analysis, plat_os=plat.os)
        dl.info(f"deps: installed={dep_result.installed} skipped={dep_result.skipped} failed={dep_result.failed}")

        # ── Phase C12: Config Init ────────────────────────────────
        with _phase(dl, "config-init"):
            init_config(start=start)
            add_repo_to_config(repo, strategy, analysis, start=start)
        w("  Config initialized\n")

        # ── Phase C13-C14: Docker Gen ─────────────────────────────
        with _phase(dl, "docker-gen"):
            df, dc = write_generated_files(analysis, start=start)
        dl.info(f"dockerfile={df} compose={dc}")
        w(f"  Docker files generated: {df.parent}\n")

        # ── Phase D: GitHub Provisioning ──────────────────────────
        with _phase(dl, "github-provision"):
            prov = provision_github(repo)
        label_count = prov.get("labels", 0)
        dl.info(f"provision: labels={label_count} ci={prov.get('ci_workflow')} "
                f"template={prov.get('issue_template')} protection={prov.get('branch_protection')}")

        # ── Phase E: Strategy Bootstrap ───────────────────────────
        with _phase(dl, "strategy-bootstrap"):
            from dark_factory.strategies import resolve_strategy  # noqa: PLC0415
            cfg = resolve_strategy(strategy)
            dl.info(f"strategy-deps={', '.join(cfg.bootstrap_deps)}")
            w(f"  Strategy deps: {', '.join(cfg.bootstrap_deps)}\n")

        # ── Cleanup ───────────────────────────────────────────────
        import shutil  # noqa: PLC0415
        if clone_dir:
            shutil.rmtree(clone_dir, ignore_errors=True)

        dl.flush()
        w("\n  Onboarding complete!\n")
        w(f"  Repository: {repo}\n")
        w(f"  Strategy:   {strategy}\n")
        w(f"  Labels:     {label_count} created\n")
        w(f"  GITHUB_REPO={repo}\n\n")
        return 0
    except Exception as exc:  # noqa: BLE001
        dl.flush()
        if clone_dir:
            import shutil  # noqa: PLC0415
            shutil.rmtree(clone_dir, ignore_errors=True)
        w(f"\n  Onboarding failed: {exc}\n")
        w(f"  Diagnostic log: {lp}\n")
        return 1
