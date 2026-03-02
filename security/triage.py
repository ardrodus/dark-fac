"""Security triage — routes findings through Obelisk, manages human acknowledgment.

Ported from ``security-triage.sh`` (US-609).  Handles response lifecycle.
NEVER auto-heals security issues; always requires human acknowledgment.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dark_factory.security.config import SecurityConfig

logger = logging.getLogger(__name__)
_STATE_DIR = Path(".dark-factory") / "security"
_PENDING_FILE = "security-triage-pending.json"
_AUDIT_LOG = "security-triage-audit.jsonl"
_PATTERN_WINDOW_DAYS = 30
_PATTERN_THRESHOLD = 5
_VALID_RESPONSES = frozenset({"fix_it", "suppress", "ignore_run", "abort"})
_CATEGORY_ADVISORIES: dict[str, str] = {
    "sql_injection": "Frequent SQL injection findings -- recommend enabling strict mode",
    "xss": "Frequent XSS findings -- recommend output encoding review",
    "cve": "Multiple CVE findings -- check for org-wide library advisory",
    "secret_leak": "Repeated secret leaks -- recommend pre-commit hooks",
    "dependency_vuln": "Recurring dependency vulnerabilities -- recommend automated updates",
}


@dataclass(frozen=True, slots=True)
class Finding:
    """A security finding to be triaged."""
    severity: str
    category: str
    detail: str
    file: str = ""
    gate: str = ""
    cve_id: str = ""


@dataclass(frozen=True, slots=True)
class TriagedFinding:
    """A finding assigned an ID and queued for human acknowledgment."""
    id: str
    finding: Finding
    status: str = "pending"
    verdict: str = "SECURITY_BLOCK"
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class PatternAdvisory:
    """Advisory from recurring pattern detection."""
    pattern_type: str
    category: str
    count: int
    advisory: str


@dataclass(frozen=True, slots=True)
class PatternResult:
    """Result of recurring pattern detection."""
    window_days: int
    total_findings: int
    category_counts: dict[str, int]
    advisories: tuple[PatternAdvisory, ...]


@dataclass(frozen=True, slots=True)
class TriageResult:
    """Result of security triage for a batch of findings."""
    triaged: tuple[TriagedFinding, ...]
    pending_count: int
    auto_heal: bool = False
    auto_retry: bool = False


def _generate_id() -> str:
    return f"sec-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _state_dir(override: Path | None = None) -> Path:
    d = override or _STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_entry(
    event: str, finding_id: str, detail: dict[str, object],
    *, state_dir: Path | None = None,
) -> None:
    d = _state_dir(state_dir)
    entry = {"timestamp": time.time(), "event": event,
             "finding_id": finding_id, "detail": detail}
    with (d / _AUDIT_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def _read_pending(state_dir: Path | None = None) -> list[dict[str, object]]:
    d = _state_dir(state_dir)
    path = d / _PENDING_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("findings", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_pending(findings: list[dict[str, object]], *, state_dir: Path | None = None) -> None:
    d = _state_dir(state_dir)
    (d / _PENDING_FILE).write_text(
        json.dumps({"findings": findings}, indent=2) + "\n", encoding="utf-8",
    )


def _save_to_mem_log(text: str, *, state_dir: Path | None = None) -> None:
    d = _state_dir(state_dir)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (d / "security-mem-log.txt").open("a", encoding="utf-8") as fh:
        fh.write(f"{now}|{text}\n")


def security_triage(
    findings: list[Finding], config: SecurityConfig,
    *, state_dir: Path | None = None,
) -> TriageResult:
    """Route security findings through triage. NEVER auto-heals.

    Every finding gets ``SECURITY_BLOCK`` — pipeline pauses until human responds.
    """
    _ = config  # reserved for future policy integration
    triaged: list[TriagedFinding] = []
    pending = _read_pending(state_dir)
    for finding in findings:
        fid = _generate_id()
        tf = TriagedFinding(id=fid, finding=finding)
        triaged.append(tf)
        pending.append({
            "id": fid, "severity": finding.severity, "category": finding.category,
            "detail": finding.detail, "file": finding.file, "gate": finding.gate,
            "cve_id": finding.cve_id, "status": "pending",
            "verdict": "SECURITY_BLOCK", "timestamp": tf.timestamp,
        })
        _log_entry(
            "security_block", fid,
            {"severity": finding.severity, "category": finding.category,
             "detail": finding.detail, "file": finding.file, "gate": finding.gate},
            state_dir=state_dir,
        )
        _save_to_mem_log(
            f"[security] {finding.severity} {finding.category} in {finding.file}: {finding.detail}",
            state_dir=state_dir,
        )
        logger.warning("SECURITY_BLOCK: %s %s -- %s [%s]",
                        finding.severity, finding.category, finding.detail, fid)
    _write_pending(pending, state_dir=state_dir)
    return TriageResult(triaged=tuple(triaged), pending_count=len(pending))


def get_pending_findings(*, state_dir: Path | None = None) -> list[dict[str, object]]:
    """Return all unacknowledged findings awaiting human response."""
    return _read_pending(state_dir)


def respond_to_finding(
    finding_id: str, action: str, *,
    reason: str = "", expiry: str = "", state_dir: Path | None = None,
) -> bool:
    """Record a human response. Actions: fix_it, suppress, ignore_run, abort."""
    if action not in _VALID_RESPONSES:
        logger.error("Invalid response: %s", action)
        return False
    if action == "suppress" and (not reason or not expiry):
        logger.error("suppress requires both reason and expiry")
        return False
    pending = _read_pending(state_dir)
    if not any(f.get("id") == finding_id for f in pending):
        logger.error("Finding %s not found in pending", finding_id)
        return False
    resp = {"response": action, "reason": reason,
            "expiry": expiry, "responded_at": time.time()}
    _log_entry("human_response", finding_id, resp, state_dir=state_dir)
    if action == "fix_it":
        logger.info("FIX IT for %s -- routing to Dark Forge", finding_id)
    elif action == "suppress":
        logger.info("SUPPRESS %s -- reason: %s, expires: %s", finding_id, reason, expiry)
        _save_to_mem_log(f"[security] {finding_id} SUPPRESSED: {reason} (expires {expiry})",
                          state_dir=state_dir)
    elif action == "ignore_run":
        logger.warning("IGNORE THIS RUN for %s -- one-time override", finding_id)
        _save_to_mem_log(f"[security] {finding_id} IGNORED (one-time override)",
                          state_dir=state_dir)
    elif action == "abort":
        logger.error("ABORT for %s -- pipeline stopped", finding_id)
    pending = [f for f in pending if f.get("id") != finding_id]
    _write_pending(pending, state_dir=state_dir)
    _log_entry("resolved", finding_id, resp, state_dir=state_dir)
    return True


def detect_recurring_patterns(
    *, window_days: int = _PATTERN_WINDOW_DAYS,
    threshold: int = _PATTERN_THRESHOLD, state_dir: Path | None = None,
) -> PatternResult:
    """Identify repeated security issues across runs within *window_days*."""
    d = _state_dir(state_dir)
    log_path = d / _AUDIT_LOG
    if not log_path.is_file():
        return PatternResult(window_days=window_days, total_findings=0,
                             category_counts={}, advisories=())
    cutoff = time.time() - (window_days * 86400)
    categories: list[str] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") != "security_block":
            continue
        if entry.get("timestamp", 0) < cutoff:
            continue
        detail = entry.get("detail", {})
        cat = detail.get("category", "unknown") if isinstance(detail, dict) else "unknown"
        categories.append(cat)
    counts = dict(Counter(categories))
    advisories: list[PatternAdvisory] = []
    for cat, count in sorted(counts.items(), key=lambda x: -x[1]):
        if count >= threshold:
            advisories.append(PatternAdvisory(
                pattern_type="frequency", category=cat, count=count,
                advisory=_CATEGORY_ADVISORIES.get(cat, f"Recurring {cat} -- investigate"),
            ))
    _save_to_mem_log(
        f"[security] Pattern detection: {len(advisories)} advisories"
        f" from {len(categories)} findings in {window_days}d window",
        state_dir=state_dir,
    )
    return PatternResult(window_days=window_days, total_findings=len(categories),
                         category_counts=counts, advisories=tuple(advisories))
