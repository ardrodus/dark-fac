"""Onboarding orchestrator with rotating diagnostic log."""
from __future__ import annotations

import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_LOG_FILE = "onboarding.log"
_MAX_RUNS = 3
_SEP = "=" * 72


def _log_path(start: Path | None = None) -> Path:
    from factory.core.config_manager import resolve_config_dir  # noqa: PLC0415
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
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        return datetime.now(timezone.utc).strftime("%H:%M:%S")


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


def run_onboarding(auto_mode: bool = False, *, start: Path | None = None) -> int:  # noqa: PLR0912, PLR0915
    """Sequence all onboarding phases. Returns 0 on success, 1 on failure."""
    from factory.setup.claude_detect import (  # noqa: PLC0415
        detect_claude_model, prompt_claude_model, save_claude_model,
    )
    from factory.setup.config_init import (  # noqa: PLC0415
        add_repo_to_config, init_config, prompt_deployment_strategy,
    )
    from factory.setup.docker_gen import write_generated_files  # noqa: PLC0415
    from factory.setup.github_auth import auto_connect_github, connect_github  # noqa: PLC0415
    from factory.setup.platform import check_dependencies, detect_platform  # noqa: PLC0415
    from factory.setup.project_analyzer import (  # noqa: PLC0415
        analyze_project, confirm_or_override_analysis,
    )
    lp = _log_path(start)
    _rotate(lp)
    dl = _Log(lp)
    dl.start()
    w = sys.stdout.write
    try:
        with _phase(dl, "platform-detect"):
            plat = detect_platform()
        dl.info(f"os={plat.os} arch={plat.arch} shell={plat.shell}")
        w(f"  Platform: {plat.os}/{plat.arch} ({plat.shell})\n")

        with _phase(dl, "deps-check"):
            deps = check_dependencies(plat)
        missing = [d.name for d in deps if not d.found]
        if missing:
            dl.info(f"missing: {', '.join(missing)}")
            w(f"  Missing deps: {', '.join(missing)}\n")
        else:
            w("  All dependencies found\n")

        with _phase(dl, "claude-detect"):
            model = detect_claude_model()
            if not model and not auto_mode:
                model = prompt_claude_model()
            if model:
                save_claude_model(model)
        dl.info(f"model={model or 'none'}")
        w(f"  Claude model: {model or 'not configured'}\n")

        with _phase(dl, "github-auth"):
            gh_ok = auto_connect_github() if auto_mode else connect_github()
        if not gh_ok:
            dl.info("github auth failed -- continuing")
            w("  GitHub: not authenticated (continuing)\n")
        else:
            w("  GitHub: authenticated\n")

        with _phase(dl, "repo-connect"):
            if auto_mode:
                repo = str(Path.cwd().resolve())
            else:
                default = str(Path.cwd().resolve())
                try:
                    raw = input(f"  Repository path [{default}]: ").strip()
                except (EOFError, KeyboardInterrupt):
                    raw = ""
                repo = raw or default
        dl.info(f"repo={repo}")
        w(f"  Repository: {repo}\n")

        with _phase(dl, "analyze"):
            analysis = analyze_project(repo)
            if not auto_mode:
                analysis = confirm_or_override_analysis(analysis)
        dl.info(f"lang={analysis.language} fw={analysis.framework} strat={analysis.detected_strategy}")

        with _phase(dl, "strategy-select"):
            strategy = analysis.detected_strategy if auto_mode else prompt_deployment_strategy(analysis)
        dl.info(f"strategy={strategy}")
        w(f"  Strategy: {strategy}\n")

        with _phase(dl, "config-init"):
            init_config(start=start)
            add_repo_to_config(repo, strategy, analysis, start=start)
        w("  Config initialized\n")

        with _phase(dl, "docker-gen"):
            df, dc = write_generated_files(analysis, start=start)
        dl.info(f"dockerfile={df} compose={dc}")
        w(f"  Docker files generated: {df.parent}\n")

        with _phase(dl, "strategy-bootstrap"):
            from factory.strategies import resolve_strategy  # noqa: PLC0415
            cfg = resolve_strategy(strategy)
            dl.info(f"strategy-deps={', '.join(cfg.bootstrap_deps)}")
            w(f"  Strategy deps: {', '.join(cfg.bootstrap_deps)}\n")

        dl.flush()
        w("\n  Onboarding complete!\n")
        return 0
    except Exception as exc:  # noqa: BLE001
        dl.flush()
        w(f"\n  Onboarding failed: {exc}\n")
        w(f"  Diagnostic log: {lp}\n")
        return 1
