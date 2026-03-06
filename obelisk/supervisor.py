"""Obelisk Supervisor — spawn, monitor, and restart Dark Factory.

Thin wiring only: spawns Dark Factory as a subprocess, runs a health
check loop, and restarts on crash with checkpoint resume if available.
Feeds status to TUI dashboard via ``obelisk-status.json``.

Crash loop protection: if the subprocess crashes 3 times within a
5-minute sliding window, the supervisor stops restarting and sets
``status`` to ``crash_loop``.  Manual intervention (restarting Obelisk)
is required to resume.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dark_factory.ui.cli_colors import cprint, print_error, print_stage_result, spinner

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

HEALTH_CHECK_INTERVAL = 30  # seconds
STATUS_FILE = "obelisk-status.json"
CHECKPOINT_FILE = ".dark-factory/.checkpoint.json"

# Crash loop thresholds
CRASH_LOOP_THRESHOLD = 3  # number of crashes to trigger crash loop
CRASH_LOOP_WINDOW = 300.0  # sliding window in seconds (5 minutes)


# ── Data types ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InvestigationSummary:
    """Compact summary of a completed investigation for status reporting."""

    id: str
    verdict: str
    timestamp: float
    url: str = ""


@dataclass(slots=True)
class SupervisorState:
    """Mutable state for the supervisor loop."""

    pid: int | None = None
    restarts: int = 0
    status: str = "idle"  # idle | watching | investigating | crashed | stopped | crash_loop
    last_health_check: float = 0.0
    last_crash_time: float = 0.0
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    investigations: list[InvestigationSummary] = field(default_factory=list)
    crash_timestamps: list[float] = field(default_factory=list)


# ── Status file ──────────────────────────────────────────────────────


def _write_status(state: SupervisorState, repo: str, status_dir: Path) -> None:
    """Write current supervisor state to obelisk-status.json for TUI."""
    status_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    uptime = now - state.start_time if state.start_time > 0 else 0.0
    payload = {
        "status": state.status,
        "dark_factory_pid": state.pid,
        "uptime_s": round(uptime, 1),
        "crash_count": state.restarts,
        "investigations": [
            {"id": inv.id, "verdict": inv.verdict, "timestamp": inv.timestamp, "url": inv.url}
            for inv in state.investigations[-10:]  # keep last 10
        ],
        "repo": repo,
        "timestamp": now,
    }
    path = status_dir / STATUS_FILE
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ── Checkpoint detection ─────────────────────────────────────────────


def _find_checkpoint(factory_repo: Path) -> Path | None:
    """Return checkpoint path if a resumable checkpoint exists."""
    ckpt = factory_repo / CHECKPOINT_FILE
    if ckpt.is_file():
        try:
            data = json.loads(ckpt.read_text(encoding="utf-8"))
            if data.get("status") in ("running", "cancelled"):
                return ckpt
        except (json.JSONDecodeError, OSError):
            pass
    return None


# ── Subprocess spawning ──────────────────────────────────────────────


def _build_command(repo: str, *, checkpoint: Path | None = None) -> list[str]:
    """Build the CLI command to launch Dark Factory."""
    cmd = [sys.executable, "-m", "dark_factory", "--auto", "--repo", repo]
    if checkpoint:
        cmd.extend(["--resume", str(checkpoint)])
    return cmd


def _spawn_factory(
    cmd: list[str],
    factory_repo: Path,
) -> subprocess.Popen[bytes]:
    """Spawn Dark Factory as a subprocess."""
    return subprocess.Popen(
        cmd,
        cwd=str(factory_repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# ── Crash loop detection ─────────────────────────────────────────────


def _record_crash(state: SupervisorState, now: float | None = None) -> bool:
    """Record a crash timestamp and return True if a crash loop is detected.

    A crash loop is ``CRASH_LOOP_THRESHOLD`` crashes within the last
    ``CRASH_LOOP_WINDOW`` seconds.
    """
    ts = now if now is not None else time.time()
    state.crash_timestamps.append(ts)
    cutoff = ts - CRASH_LOOP_WINDOW
    state.crash_timestamps = [t for t in state.crash_timestamps if t > cutoff]
    return len(state.crash_timestamps) >= CRASH_LOOP_THRESHOLD


def _trigger_crash_loop_investigation(
    state: SupervisorState,
    repo: str,
    status_dir: Path,
) -> None:
    """Handle crash loop: update status, log, and trigger investigation.

    Fires an investigation of the crash pattern by calling the investigator
    asynchronously (best-effort).  The supervisor itself does NOT block on
    the investigation result.
    """
    state.status = "crash_loop"
    _write_status(state, repo, status_dir)

    cprint("CRASH LOOP DETECTED — stopping automatic restarts", "error")
    cprint(
        f"  {len(state.crash_timestamps)} crashes in the last "
        f"{CRASH_LOOP_WINDOW:.0f}s window",
        "error",
    )
    cprint("  Manual restart of Obelisk required to resume", "warning")

    # Best-effort investigation of the crash pattern
    try:
        from dark_factory.obelisk.models import Alert  # noqa: PLC0415

        alert = Alert(
            error_type="CRASH_LOOP",
            source="supervisor",
            pipeline="obelisk",
            node="supervisor",
            message=f"Crash loop detected: {len(state.crash_timestamps)} crashes in {CRASH_LOOP_WINDOW:.0f}s. "
            f"Last errors: {'; '.join(state.errors[-3:])}",
            signature=f"crash-loop-{repo}",
        )
        logger.info("Triggering investigation for crash loop pattern: %s", alert.signature)
        state.investigations.append(
            InvestigationSummary(
                id=f"crash-loop-{int(time.time())}",
                verdict="CRASH_LOOP",
                timestamp=time.time(),
                url="",
            ),
        )
        _write_status(state, repo, status_dir)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to trigger crash loop investigation", exc_info=True)


# ── Health check ─────────────────────────────────────────────────────


def _is_alive(proc: subprocess.Popen[bytes]) -> bool:
    """Check if the subprocess is still running."""
    return proc.poll() is None


# ── Main supervisor loop ─────────────────────────────────────────────


def run_supervisor(
    repo: str,
    factory_repo: str | Path | None = None,
) -> None:
    """Spawn Dark Factory and keep it running.

    Parameters
    ----------
    repo:
        GitHub repository (``owner/repo``) to process.
    factory_repo:
        Path to the Dark Factory working directory.
        Defaults to the current working directory.
    """
    factory_path = Path(factory_repo) if factory_repo else Path.cwd()
    status_dir = factory_path / ".dark-factory"
    state = SupervisorState()

    cprint("Obelisk Supervisor starting", "info")
    cprint(f"  repo: {repo}", "info")
    cprint(f"  cwd:  {factory_path}", "info")

    state.start_time = time.time()

    try:
        while True:
            # Check for checkpoint resume on (re)start
            checkpoint = _find_checkpoint(factory_path)
            if checkpoint:
                cprint("Checkpoint found — resuming from last state", "warning")

            cmd = _build_command(repo, checkpoint=checkpoint)
            state.status = "watching"
            _write_status(state, repo, status_dir)

            with spinner("Spawning Dark Factory"):
                proc = _spawn_factory(cmd, factory_path)
            state.pid = proc.pid

            print_stage_result(
                "Dark Factory",
                "running",
                f"pid={proc.pid}" + (f" restart #{state.restarts}" if state.restarts else ""),
            )
            _write_status(state, repo, status_dir)

            # Health check loop
            while _is_alive(proc):
                state.last_health_check = time.time()
                state.status = "watching"
                _write_status(state, repo, status_dir)
                time.sleep(HEALTH_CHECK_INTERVAL)

            # Process exited — check exit code
            exit_code = proc.returncode
            state.pid = None

            if exit_code == 0:
                print_stage_result("Dark Factory", "PASS", "exited cleanly")
                state.status = "stopped"
                _write_status(state, repo, status_dir)
                return

            # Crash detected
            state.status = "crashed"
            state.last_crash_time = time.time()
            state.restarts += 1
            error_msg = f"exit code {exit_code}"

            # Capture stderr tail for diagnostics
            if proc.stderr:
                try:
                    stderr_tail = proc.stderr.read(4096).decode("utf-8", errors="replace").strip()
                    if stderr_tail:
                        error_msg += f": {stderr_tail[-200:]}"
                except OSError:
                    pass

            state.errors.append(error_msg)
            print_stage_result("Dark Factory", "FAIL", f"crashed ({error_msg})")

            # Check for crash loop (3 crashes in 5 minutes)
            if _record_crash(state):
                _trigger_crash_loop_investigation(state, repo, status_dir)
                return

            cprint(f"Restarting (attempt #{state.restarts})...", "warning")

            _write_status(state, repo, status_dir)

    except KeyboardInterrupt:
        cprint("\nSupervisor interrupted — shutting down", "warning")
        state.status = "stopped"
        _write_status(state, repo, status_dir)

        # Terminate child if still running
        if state.pid is not None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()

        print_stage_result("Obelisk Supervisor", "PASS", "stopped")
    except Exception as exc:
        print_error(str(exc), hint="Check factory_repo path and Dark Factory installation.")
        state.status = "stopped"
        state.errors.append(str(exc))
        _write_status(state, repo, status_dir)
        raise
