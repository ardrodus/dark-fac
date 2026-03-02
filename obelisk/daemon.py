"""Obelisk daemon — background health monitor (ports obelisk-daemon.sh).

Daemon thread checking container health, disk space, rate-limit budget, and
stale workspaces.  Anomalies trigger auto-healing.  Status written to
``.dark-factory/obelisk/daemon-status.json``.
"""
from __future__ import annotations

import json, logging, shutil, threading, time  # noqa: E401
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from factory.integrations.shell import CommandResult, docker, gh

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
_STATE_DIR = Path(".dark-factory")
_STATUS_DIR = _STATE_DIR / "obelisk"
_DEFAULT_INTERVAL = 60.0
_DISK_THR, _RATE_THR, _WS_TTL = 10, 100, 7 * 24 * 3600


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """Single health-check outcome."""
    name: str
    healthy: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DaemonStatus:
    """Snapshot written to daemon-status.json."""
    running: bool
    system_status: str
    last_check: float
    checks: tuple[HealthCheckResult, ...]
    heal_actions: tuple[str, ...] = ()
    started_at: float = 0.0


def _check_containers(*, docker_fn: Callable[..., CommandResult] | None = None) -> HealthCheckResult:
    r = (docker_fn or docker)(["ps", "--format", "{{.Names}}\t{{.Status}}"], check=False)
    if r.returncode != 0:
        return HealthCheckResult("containers", False, "Docker not responsive")
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    bad = [ln for ln in lines if "unhealthy" in ln.lower() or "exited" in ln.lower()]
    if bad:
        return HealthCheckResult("containers", False, f"unhealthy: {', '.join(ln.split(chr(9))[0] for ln in bad)}")
    return HealthCheckResult("containers", True, f"{len(lines)} ok")

def _check_disk(*, threshold: int = _DISK_THR) -> HealthCheckResult:
    try:
        u = shutil.disk_usage(".")
        free = int(u.free * 100 / u.total)
    except OSError as exc:
        return HealthCheckResult("disk", True, f"skipped: {exc}")
    if free < threshold:
        return HealthCheckResult("disk", False, f"{free}% free (<{threshold}%)")
    return HealthCheckResult("disk", True, f"{free}% free")

def _check_rate_limit(
    *, threshold: int = _RATE_THR, gh_fn: Callable[..., CommandResult] | None = None,
) -> HealthCheckResult:
    r = (gh_fn or gh)(["api", "rate_limit", "--jq", ".rate.remaining"], check=False)
    if r.returncode != 0:
        return HealthCheckResult("rate_limit", True, "gh unavailable")
    try:
        remaining = int(r.stdout.strip())
    except (ValueError, AttributeError):
        return HealthCheckResult("rate_limit", True, "parse error")
    if remaining < threshold:
        return HealthCheckResult("rate_limit", False, f"{remaining} (<{threshold})")
    return HealthCheckResult("rate_limit", True, f"{remaining} remaining")

def _check_stale_workspaces(*, ttl: float = _WS_TTL, state_dir: Path = _STATE_DIR) -> HealthCheckResult:
    cache = state_dir / "workspaces" / "workspace_cache.json"
    if not cache.is_file():
        return HealthCheckResult("stale_workspaces", True, "no cache")
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return HealthCheckResult("stale_workspaces", True, "unreadable")
    now = time.time()
    stale = [n for n, ws in data.items() if isinstance(ws, dict)
             and isinstance(ws.get("created_at"), (int, float)) and (now - ws["created_at"]) > ttl]
    if stale:
        return HealthCheckResult("stale_workspaces", False, f"{len(stale)} stale: {', '.join(stale[:3])}")
    return HealthCheckResult("stale_workspaces", True, "all fresh")

def _heal(check: HealthCheckResult) -> str | None:
    """Attempt auto-healing for a failed check."""
    if check.name == "containers" and "unhealthy" in check.detail:
        names = check.detail.removeprefix("unhealthy: ").split(", ")
        for n in names[:3]:
            docker(["restart", n.strip()], check=False)
        return f"restarted: {', '.join(names[:3])}"
    if check.name == "stale_workspaces":
        return "flagged stale workspaces for cleanup"
    return None

def _write_status(status: DaemonStatus, *, status_dir: Path | None = None) -> None:
    d = status_dir or _STATUS_DIR
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "running": status.running, "system_status": status.system_status,
        "last_check": status.last_check, "started_at": status.started_at,
        "checks": [{"name": c.name, "healthy": c.healthy, "detail": c.detail} for c in status.checks],
        "heal_actions": list(status.heal_actions),
    }
    (d / "daemon-status.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

def _read_status(*, status_dir: Path | None = None) -> DaemonStatus | None:
    path = (status_dir or _STATUS_DIR) / "daemon-status.json"
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        checks = tuple(HealthCheckResult(c["name"], c["healthy"], c.get("detail", "")) for c in d.get("checks", []))
        return DaemonStatus(running=d.get("running", False), system_status=d.get("system_status", "unknown"),
                            last_check=d.get("last_check", 0.0), checks=checks,
                            heal_actions=tuple(d.get("heal_actions", ())), started_at=d.get("started_at", 0.0))
    except (json.JSONDecodeError, KeyError, OSError):
        return None


class ObeliskDaemon:
    """Background health monitor running periodic checks in a daemon thread."""

    def __init__(self, *, interval: float = _DEFAULT_INTERVAL,
                 state_dir: Path | None = None, status_dir: Path | None = None,
                 docker_fn: Callable[..., CommandResult] | None = None,
                 gh_fn: Callable[..., CommandResult] | None = None) -> None:
        self._interval = interval
        self._state_dir = state_dir or _STATE_DIR
        self._status_dir = status_dir or _STATUS_DIR
        self._docker_fn, self._gh_fn = docker_fn, gh_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0

    def start(self) -> bool:
        """Start the daemon. Returns False if already running."""
        if self.is_running():
            return False
        self._stop.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="obelisk-daemon")
        self._thread.start()
        logger.info("Obelisk daemon started (interval=%.0fs)", self._interval)
        return True

    def stop(self) -> bool:
        """Stop the daemon gracefully. Returns False if not running."""
        if not self.is_running():
            return False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 5)
            self._thread = None
        _write_status(DaemonStatus(running=False, system_status="stopped", last_check=time.time(),
                                   checks=(), started_at=self._started_at), status_dir=self._status_dir)
        logger.info("Obelisk daemon stopped")
        return True

    def is_running(self) -> bool:
        """True if the daemon thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._run_cycle()
            self._stop.wait(self._interval)

    def _run_cycle(self) -> None:
        checks = [_check_containers(docker_fn=self._docker_fn), _check_disk(),
                   _check_rate_limit(gh_fn=self._gh_fn), _check_stale_workspaces(state_dir=self._state_dir)]
        heals: list[str] = []
        for chk in checks:
            if not chk.healthy:
                action = _heal(chk)
                if action:
                    heals.append(action)
                    logger.info("Healed: %s", action)
        _write_status(DaemonStatus(
            running=True, system_status="healthy" if all(c.healthy for c in checks) else "degraded",
            last_check=time.time(), checks=tuple(checks),
            heal_actions=tuple(heals), started_at=self._started_at), status_dir=self._status_dir)
