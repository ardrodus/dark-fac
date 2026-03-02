"""Security posture dashboard — unified view of gate results and scan history.

Ported from ``security-dashboard.sh`` (US-610).  Integrates with Textual TUI.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.widgets import DataTable, Label, Static

from factory.security.config import SecurityConfig
from factory.ui.theme import THEME

logger = logging.getLogger(__name__)
_GATE_NAMES = ("secret-scan", "dependency-scan", "sast-scan",
               "image-scan", "sbom-scan", "ai-review")


@dataclass(frozen=True, slots=True)
class GateStatus:
    """Status of a single security gate."""
    gate: str
    status: str  # "PASS" | "FAIL" | "NOT_RUN"
    findings_count: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SeverityCounts:
    """Finding counts grouped by severity level."""
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low + self.info


@dataclass(frozen=True, slots=True)
class ScanHistoryEntry:
    """A single scan history entry with timestamp and pass/fail per gate."""
    timestamp: str
    overall_status: str
    findings: SeverityCounts
    gate_statuses: dict[str, str]


@dataclass(frozen=True, slots=True)
class SecurityPosture:
    """Unified security posture aggregating all gate results."""
    overall_status: str  # "PASS" | "FAIL" | "WARN" | "INCOMPLETE"
    gates: tuple[GateStatus, ...]
    findings: SeverityCounts
    scan_history: tuple[ScanHistoryEntry, ...]
    timestamp: str


def _verdict_to_gates(verdict: dict[str, Any]) -> tuple[GateStatus, ...]:
    raw = verdict.get("gate_results", {})
    result: list[GateStatus] = []
    for name in _GATE_NAMES:
        gr = raw.get(name, {})
        if not gr:
            result.append(GateStatus(gate=name, status="NOT_RUN", findings_count=0))
        else:
            result.append(GateStatus(
                gate=name, status="PASS" if gr.get("passed", False) else "FAIL",
                findings_count=gr.get("findings_count", 0), detail=gr.get("summary", ""),
            ))
    return tuple(result)


def _count_findings(verdict: dict[str, Any]) -> SeverityCounts:
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in (*verdict.get("blocking_findings", []), *verdict.get("advisory_findings", [])):
        sev = f.get("severity", "info").lower()
        if sev in counts:
            counts[sev] += 1
    return SeverityCounts(**counts)


def _build_history(security_dir: Path) -> tuple[ScanHistoryEntry, ...]:
    """Build scan history from all sentinel verdict files."""
    if not security_dir.is_dir():
        return ()
    scored: list[tuple[float, ScanHistoryEntry]] = []
    for vf in security_dir.glob("*/sentinel-verdict.json"):
        try:
            v = json.loads(vf.read_text(encoding="utf-8"))
            mtime = vf.stat().st_mtime
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(v, dict) or not v:
            continue
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        gs = {n: ("PASS" if g.get("passed") else "FAIL")
              for n, g in v.get("gate_results", {}).items()}
        scored.append((mtime, ScanHistoryEntry(
            timestamp=ts, overall_status="PASS" if v.get("passed") else "FAIL",
            findings=_count_findings(v), gate_statuses=gs)))
    scored.sort(key=lambda x: x[0])
    return tuple(e for _, e in scored[-50:])


def collect_security_data(
    config: SecurityConfig, *, workspace_path: str = ".",
) -> SecurityPosture:
    """Aggregate all security gate results into a unified posture view."""
    _ = config  # reserved for mode-aware filtering
    security_dir = Path(workspace_path) / ".dark-factory" / "security"
    # Find latest verdict
    latest: dict[str, Any] = {}
    latest_mtime = 0.0
    if security_dir.is_dir():
        for vf in security_dir.glob("*/sentinel-verdict.json"):
            try:
                mtime = vf.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest = json.loads(vf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    gates = _verdict_to_gates(latest)
    findings = _count_findings(latest)
    history = _build_history(security_dir)
    statuses = [g.status for g in gates]
    if any(s == "FAIL" for s in statuses):
        overall = "FAIL"
    elif all(s == "NOT_RUN" for s in statuses):
        overall = "INCOMPLETE"
    elif any(s == "NOT_RUN" for s in statuses):
        overall = "WARN"
    else:
        overall = "PASS"
    posture = SecurityPosture(overall_status=overall, gates=gates, findings=findings,
                              scan_history=history, timestamp=datetime.now(
                                  timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    logger.info("Security posture: %s (%d findings)", overall, findings.total)
    return posture


_STATUS_CLR: dict[str, str] = {"PASS": THEME.success, "FAIL": THEME.error,
                                "WARN": THEME.warning, "NOT_RUN": THEME.text_muted,
                                "INCOMPLETE": THEME.warning}


class SecurityPanel(Static):
    """Textual TUI panel showing unified security posture."""

    def compose(self) -> ComposeResult:
        yield Label("[b]Security Posture[/b]")
        yield DataTable(id="sec-gate-table")
        yield Label("[b]Findings by Severity[/b]")
        yield DataTable(id="sec-findings-table")
        yield Label("[b]Scan History[/b]")
        yield DataTable(id="sec-history-table")

    def on_mount(self) -> None:
        gt: DataTable[Any] = self.query_one("#sec-gate-table", DataTable)
        gt.add_columns("Gate", "Status", "Findings", "Detail")
        gt.cursor_type = "none"
        ft: DataTable[Any] = self.query_one("#sec-findings-table", DataTable)
        ft.add_columns("Severity", "Count")
        ft.cursor_type = "none"
        ht: DataTable[Any] = self.query_one("#sec-history-table", DataTable)
        ht.add_columns("Timestamp", "Status", "Crit", "High", "Med", "Low")
        ht.cursor_type = "none"

    def refresh_posture(self, posture: SecurityPosture) -> None:
        """Update all tables from a SecurityPosture snapshot."""
        gt: DataTable[Any] = self.query_one("#sec-gate-table", DataTable)
        gt.clear()
        for g in posture.gates:
            c = _STATUS_CLR.get(g.status, THEME.text)
            gt.add_row(g.gate, f"[{c}]{g.status}[/]", str(g.findings_count), g.detail[:50])
        ft: DataTable[Any] = self.query_one("#sec-findings-table", DataTable)
        ft.clear()
        for sev, cnt, clr in (("critical", posture.findings.critical, THEME.error),
                               ("high", posture.findings.high, THEME.warning),
                               ("medium", posture.findings.medium, THEME.info),
                               ("low", posture.findings.low, THEME.text_muted),
                               ("info", posture.findings.info, THEME.text_muted)):
            ft.add_row(sev, f"[{clr}]{cnt}[/]")
        ht: DataTable[Any] = self.query_one("#sec-history-table", DataTable)
        ht.clear()
        for e in posture.scan_history[-10:]:
            c = _STATUS_CLR.get(e.overall_status, THEME.text)
            ht.add_row(e.timestamp, f"[{c}]{e.overall_status}[/]", str(e.findings.critical),
                       str(e.findings.high), str(e.findings.medium), str(e.findings.low))
