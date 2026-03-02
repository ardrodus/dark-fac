"""Runtime security monitoring — process auditing, file integrity, resource checks.

Ported from ``runtime-security.sh`` (US-020 / US-612).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from dark_factory.integrations.shell import docker

logger = logging.getLogger(__name__)
_CPU_SPIKE_PCT, _MEM_SPIKE_PCT, _DISK_SPIKE_MB = 90.0, 95.0, 500

_MINER_PATTERNS: tuple[str, ...] = (
    "xmrig", "minerd", "cpuminer", "cgminer", "bfgminer", "ethminer",
    "nbminer", "t-rex", "gminer", "lolminer", "phoenixminer",
    "stratum+tcp", "stratum+ssl", "cryptonight", "randomx",
)
_REVSHELL_PATTERNS: tuple[str, ...] = (
    "bash -i >& /dev/tcp", "bash -i >&/dev/tcp",
    r"nc -e /bin", "ncat -e", r"python.*pty.spawn",
    r"python.*socket.*connect", r"perl.*socket.*INET",
    r"ruby.*TCPSocket", r"php.*fsockopen", r"socat.*TCP",
    "/dev/tcp/", r"mkfifo.*nc",
)
_BASELINE_DIRS: tuple[str, ...] = ("/usr/bin", "/usr/sbin", "/bin", "/sbin")


@dataclass(frozen=True, slots=True)
class Finding:
    """A single runtime-security finding."""
    severity: str
    category: str
    container: str
    detail: str
    action: str = "flagged"
    ts: str = field(default_factory=lambda: _now_utc())


def _now_utc() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class Baseline:
    """Captured initial state for a container."""
    container_id: str
    processes: frozenset[str]
    file_checksums: dict[str, str]
    ts: str = field(default_factory=_now_utc)


@dataclass(frozen=True, slots=True)
class PulseResult:
    """Aggregated result from a full security pulse."""
    container_id: str
    findings: tuple[Finding, ...]
    clean: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "clean", len(self.findings) == 0)


def baseline_container(container_id: str) -> Baseline:
    """Capture initial state (processes, file checksums) for *container_id*."""
    procs = _snapshot_processes(container_id)
    checksums = _snapshot_checksums(container_id)
    logger.info("Baseline captured for %s: %d procs, %d files", container_id, len(procs), len(checksums))
    return Baseline(container_id=container_id, processes=frozenset(procs), file_checksums=checksums)


def _snapshot_processes(container_id: str) -> set[str]:
    result = docker(["exec", container_id, "ps", "aux"])
    if result.returncode != 0:
        return set()
    return {ln.strip() for ln in result.stdout.strip().splitlines()[1:] if ln.strip()}


def _snapshot_checksums(container_id: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for d in _BASELINE_DIRS:
        result = docker(["exec", container_id, "find", d, "-type", "f", "-exec", "sha256sum", "{}", ";"],
                        timeout=120)
        if result.returncode != 0:
            continue
        for line in result.stdout.strip().splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                checksums[parts[1]] = parts[0]
    return checksums


def check_processes(container_id: str, baseline: Baseline) -> list[Finding]:
    """Detect unexpected / suspicious processes vs *baseline*."""
    findings: list[Finding] = []
    result = docker(["exec", container_id, "ps", "aux"])
    if result.returncode != 0:
        return findings
    proc_text = result.stdout.lower()

    for pat in _MINER_PATTERNS:
        if pat.lower() in proc_text:
            findings.append(Finding("critical", "cryptominer", container_id,
                                    f"Cryptominer indicator detected: {pat}"))

    for pat in _REVSHELL_PATTERNS:
        if re.search(pat, proc_text, re.IGNORECASE):
            findings.append(Finding("critical", "reverse_shell", container_id,
                                    f"Reverse-shell indicator detected: {pat}"))

    # Flag brand-new process lines not in the baseline
    current = _snapshot_processes(container_id)
    new_procs = current - baseline.processes
    if new_procs:
        findings.append(Finding("low", "new_processes", container_id,
                                f"{len(new_procs)} new process(es) since baseline"))
    return findings


def check_file_integrity(container_id: str, baseline: Baseline) -> list[Finding]:
    """Detect unauthorized file changes vs *baseline*."""
    findings: list[Finding] = []
    if not baseline.file_checksums:
        return findings
    current = _snapshot_checksums(container_id)
    modified = [p for p, h in baseline.file_checksums.items() if current.get(p, h) != h]
    removed = [p for p in baseline.file_checksums if p not in current]
    added = [p for p in current if p not in baseline.file_checksums]

    if modified:
        findings.append(Finding("high", "file_modified", container_id,
                                f"{len(modified)} file(s) modified: {', '.join(modified[:5])}",
                                action="container_paused"))
    if removed:
        findings.append(Finding("high", "file_removed", container_id,
                                f"{len(removed)} file(s) removed: {', '.join(removed[:5])}",
                                action="container_paused"))
    if added:
        findings.append(Finding("medium", "file_added", container_id,
                                f"{len(added)} new file(s): {', '.join(added[:5])}"))
    return findings


def check_resources(container_id: str) -> list[Finding]:
    """Detect CPU / memory / disk resource abuse."""
    findings: list[Finding] = []
    result = docker(["stats", container_id, "--no-stream",
                     "--format", "{{.CPUPerc}}|{{.MemPerc}}"])
    if result.returncode != 0 or not result.stdout.strip():
        return findings

    parts = result.stdout.strip().split("|")
    if len(parts) < 2:
        return findings

    def _pct(s: str) -> float:
        try:
            return float(s.strip().rstrip("%"))
        except ValueError:
            return 0.0

    cpu, mem = _pct(parts[0]), _pct(parts[1])

    if cpu >= _CPU_SPIKE_PCT:
        findings.append(Finding("medium", "cpu_spike", container_id,
                                f"CPU at {cpu:.1f}% (threshold {_CPU_SPIKE_PCT}%)"))
    if mem >= _MEM_SPIKE_PCT:
        findings.append(Finding("medium", "memory_spike", container_id,
                                f"Memory at {mem:.1f}% (threshold {_MEM_SPIKE_PCT}%)"))

    # Disk usage check via df inside container
    df_result = docker(["exec", container_id, "df", "-m", "/"])
    if df_result.returncode == 0:
        for line in df_result.stdout.strip().splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 3:
                try:
                    used_mb = int(cols[2])
                except ValueError:
                    continue
                if used_mb >= _DISK_SPIKE_MB:
                    findings.append(Finding("medium", "disk_spike", container_id,
                                            f"Disk usage {used_mb} MB (threshold {_DISK_SPIKE_MB} MB)"))
                break
    return findings


def security_pulse(container_id: str, baseline: Baseline) -> PulseResult:
    """Run all runtime-security checks and return aggregated result."""
    all_findings: list[Finding] = []
    all_findings.extend(check_processes(container_id, baseline))
    all_findings.extend(check_file_integrity(container_id, baseline))
    all_findings.extend(check_resources(container_id))
    logger.info("Security pulse for %s: %d finding(s)", container_id, len(all_findings))
    return PulseResult(container_id=container_id, findings=tuple(all_findings))
