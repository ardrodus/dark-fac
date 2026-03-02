"""Auto-healing playbooks — idempotent remediation for common failure modes.

Ports auto-heal.sh and self-heal-infra.sh into a playbook-based system.
Each playbook has a name, trigger condition, remediation function, and cooldown
to prevent re-running the same fix too frequently.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from factory.integrations.shell import docker, gh

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HealResult:
    """Outcome of a single playbook execution."""
    playbook_name: str
    success: bool
    before_state: str
    after_state: str
    detail: str = ""


@dataclass(slots=True)
class Playbook:
    """A named, idempotent healing action with cooldown enforcement."""
    name: str
    trigger_condition: str
    remediation_fn: Callable[[], HealResult]
    cooldown_seconds: int = 300


@dataclass(slots=True)
class _CooldownTracker:
    """In-memory record of last-run timestamps per playbook."""
    _last_run: dict[str, float] = field(default_factory=dict)

    def is_allowed(self, name: str, cooldown: int) -> bool:
        return (time.time() - self._last_run.get(name, 0.0)) >= cooldown

    def record(self, name: str) -> None:
        self._last_run[name] = time.time()


_tracker = _CooldownTracker()

# -- Built-in remediation functions ------------------------------------------

def _docker_ps() -> str:
    r = docker(["ps", "-a", "--format", "{{.Names}}\t{{.Status}}"], check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def _restart_containers() -> HealResult:
    before = _docker_ps() or "docker unavailable"
    targets = [ln.split("\t")[0] for ln in before.splitlines()
               if "\t" in ln and ("exited" in ln.lower() or "unhealthy" in ln.lower())]
    for name in targets[:5]:
        docker(["restart", name], check=False)
    after = _docker_ps() or "unknown"
    ok = not any("exited" in ln.lower() for ln in after.splitlines())
    return HealResult("restart_containers", ok, before, after, f"restarted {len(targets)}")


def _refresh_twins() -> HealResult:
    ps = docker(["compose", "ps", "--format", "{{.Name}}\t{{.Status}}"], check=False)
    before = ps.stdout.strip() if ps.returncode == 0 else "compose unavailable"
    r = docker(["compose", "up", "-d"], check=False)
    ps2 = docker(["compose", "ps", "--format", "{{.Name}}\t{{.Status}}"], check=False)
    after = ps2.stdout.strip() if ps2.returncode == 0 else "unknown"
    return HealResult("refresh_twins", r.returncode == 0, before, after,
                      r.stderr.strip()[:120] if r.returncode != 0 else "twins refreshed")


def _reset_network() -> HealResult:
    net = "df-net"
    before = "exists" if docker(["network", "inspect", net], check=False).returncode == 0 else "missing"
    if before == "missing":
        docker(["network", "create", net], check=False)
    ok = docker(["network", "inspect", net], check=False).returncode == 0
    return HealResult("reset_network", ok, before, "exists" if ok else "still missing")


def _clear_stale_workspaces() -> HealResult:
    def _exited_count() -> int:
        r = docker(["ps", "-a", "-q", "--filter", "status=exited"], check=False)
        return len(r.stdout.strip().splitlines()) if r.returncode == 0 and r.stdout.strip() else 0
    before_n = _exited_count()
    docker(["container", "prune", "--force"], check=False)
    after_n = _exited_count()
    return HealResult("clear_stale_workspaces", after_n < before_n or before_n == 0,
                      f"{before_n} exited", f"{after_n} exited")


def _refresh_credentials() -> HealResult:
    before_ok = gh(["auth", "status"], check=False).returncode == 0
    if not before_ok:
        gh(["auth", "refresh"], check=False)
    after_ok = gh(["auth", "status"], check=False).returncode == 0
    return HealResult("refresh_credentials", after_ok,
                      "authenticated" if before_ok else "unauthenticated",
                      "authenticated" if after_ok else "still unauthenticated")


def _prune_docker() -> HealResult:
    def _df() -> str:
        r = docker(["system", "df", "--format", "{{.Type}}\t{{.Reclaimable}}"], check=False)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    before = _df()
    docker(["system", "prune", "--force", "--filter", "until=48h"], check=False)
    return HealResult("prune_docker", True, before, _df(), "pruned resources older than 48h")


# -- Playbook registry -------------------------------------------------------

PLAYBOOKS: tuple[Playbook, ...] = (
    Playbook("restart_containers", "container exited or unhealthy", _restart_containers, 120),
    Playbook("refresh_twins", "twin service not running", _refresh_twins, 180),
    Playbook("reset_network", "docker network missing", _reset_network, 300),
    Playbook("clear_stale_workspaces", "stale or exited workspace containers", _clear_stale_workspaces, 300),
    Playbook("refresh_credentials", "gh auth failure or token expired", _refresh_credentials, 600),
    Playbook("prune_docker", "disk space low", _prune_docker, 900),
)


def get_playbook(name: str) -> Playbook | None:
    """Look up a built-in playbook by name."""
    for pb in PLAYBOOKS:
        if pb.name == name:
            return pb
    return None


# -- Execution engine ---------------------------------------------------------

def run_playbook(
    playbook: Playbook, *, force: bool = False, tracker: _CooldownTracker | None = None,
) -> HealResult | None:
    """Execute *playbook* if its cooldown has elapsed.

    Returns ``None`` when still within the cooldown period (unless *force*).
    """
    trk = tracker or _tracker
    if not force and not trk.is_allowed(playbook.name, playbook.cooldown_seconds):
        logger.info("Playbook %s skipped (cooldown %ds)", playbook.name, playbook.cooldown_seconds)
        return None

    logger.info("Running playbook: %s (trigger: %s)", playbook.name, playbook.trigger_condition)
    result = playbook.remediation_fn()
    trk.record(playbook.name)
    logger.info("Playbook %s %s | before=%s | after=%s",
                result.playbook_name, "ok" if result.success else "FAIL",
                result.before_state[:80], result.after_state[:80])
    return result


def run_all(*, force: bool = False) -> list[HealResult]:
    """Run every built-in playbook that is off cooldown."""
    return [r for pb in PLAYBOOKS if (r := run_playbook(pb, force=force)) is not None]
