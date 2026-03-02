"""Gate base framework — shared infrastructure for all validation gates.

Provides :class:`GateRunner`, the single entry-point for registering and
executing named checks with standard output formatting, timeout handling,
transient-failure retry, prerequisite skipping, and metrics persistence.

Also includes shared file helpers and the gate orchestration registry.
"""

from __future__ import annotations

import importlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

logger = logging.getLogger(__name__)

# ── Shared constants ─────────────────────────────────────────────

API_EXTS: tuple[str, ...] = ("yaml", "graphql", "proto")
SCHEMA_EXTS: tuple[str, ...] = ("sql", "json")
IFACE_EXTS: tuple[str, ...] = ("ts", "py", "go", "rs", "java", "rb", "js")

_METRICS_DIR = ".dark-factory"
_METRICS_FILE = "gate-metrics.json"
_DEFAULT_TIMEOUT = 120.0
_DEFAULT_RETRIES = 0
_DEFAULT_RETRY_DELAY = 1.0


class CheckStatus(Enum):
    """Possible outcomes of a single gate check."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    WARN = "WARN"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a single named check."""

    name: str
    status: CheckStatus
    duration_ms: float
    details: str


@dataclass(frozen=True, slots=True)
class GateReport:
    """Aggregated results from a full gate run."""

    gate_name: str
    checks: tuple[CheckResult, ...]
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "passed",
            all(c.status in (CheckStatus.PASS, CheckStatus.SKIP, CheckStatus.WARN)
                for c in self.checks),
        )


@dataclass(frozen=True, slots=True)
class _CheckEntry:
    """Internal descriptor for a registered check."""

    name: str
    fn: Callable[[], bool | str]
    timeout: float
    retries: int
    retry_delay: float
    prerequisite: Callable[[], bool] | None


class CheckTimeoutError(Exception):
    """Raised internally when a check exceeds its timeout budget."""


_PASS_STATUSES = frozenset({CheckStatus.PASS, CheckStatus.SKIP, CheckStatus.WARN})


class GateRunner:
    """Single entry-point for registering and executing gate checks.

    Parameters
    ----------
    gate_name:
        Human-readable name for this gate (used in reports and metrics).
    metrics_dir:
        Directory for ``gate-metrics.json``.  Defaults to ``.dark-factory``.
    """

    def __init__(self, gate_name: str, *, metrics_dir: str | Path | None = None) -> None:
        self._gate_name = gate_name
        self._checks: list[_CheckEntry] = []
        self._results: list[CheckResult] = []
        self._metrics_dir = Path(metrics_dir) if metrics_dir is not None else Path(_METRICS_DIR)
        logger.info("GateRunner initialised: %s", gate_name)

    def register_check(
        self, name: str, check_fn: Callable[[], bool | str], *,
        timeout: float = _DEFAULT_TIMEOUT, retries: int = _DEFAULT_RETRIES,
        retry_delay: float = _DEFAULT_RETRY_DELAY,
        prerequisite: Callable[[], bool] | None = None,
    ) -> None:
        """Register a named check to be executed during :meth:`run`."""
        self._checks.append(_CheckEntry(
            name=name, fn=check_fn, timeout=timeout,
            retries=retries, retry_delay=retry_delay, prerequisite=prerequisite,
        ))
        logger.debug("Registered check %r for gate %r", name, self._gate_name)

    def check(self, name: str, fn: Callable[[], bool | str]) -> CheckResult:
        """Run a single ad-hoc check (not from the registry) and record it."""
        result = self._execute_check(_CheckEntry(
            name=name, fn=fn, timeout=_DEFAULT_TIMEOUT,
            retries=_DEFAULT_RETRIES, retry_delay=_DEFAULT_RETRY_DELAY,
            prerequisite=None,
        ))
        self._results.append(result)
        return result

    def report(self) -> str:
        """Format and return a human-readable report of all results."""
        lines: list[str] = [f"Gate: {self._gate_name}", "=" * 60]
        for cr in self._results:
            line = f"  [{cr.status.value:4s}] {cr.name:<30s}  {cr.duration_ms:>8.1f}ms"
            if cr.details:
                line += f"  {cr.details}"
            lines.append(line)
        lines.append("=" * 60)
        passed = all(c.status in _PASS_STATUSES for c in self._results)
        lines.append(f"Result: {'PASSED' if passed else 'FAILED'}")
        report_text = "\n".join(lines)
        logger.info("Gate report:\n%s", report_text)
        return report_text

    def finalize(self) -> int:
        """Write metrics to ``gate-metrics.json`` and return exit code (0=pass, 1=fail)."""
        passed = all(c.status in _PASS_STATUSES for c in self._results)
        metrics = {
            "gate": self._gate_name,
            "passed": passed,
            "checks": [
                {"name": cr.name, "status": cr.status.value,
                 "duration_ms": round(cr.duration_ms, 2), "details": cr.details}
                for cr in self._results
            ],
            "timestamp": time.time(),
        }
        metrics_path = self._metrics_dir / _METRICS_FILE
        try:
            self._metrics_dir.mkdir(parents=True, exist_ok=True)
            metrics_path.write_text(
                json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            )
            logger.info("Metrics written to %s", metrics_path)
        except OSError as exc:
            logger.warning("Failed to write gate metrics: %s", exc)
        return 0 if passed else 1

    def run(self, gate_name: str | None = None) -> GateReport:
        """Execute all registered checks and return a :class:`GateReport`.

        Parameters
        ----------
        gate_name:
            Optional override for the gate name in the report.
        """
        effective_name = gate_name or self._gate_name
        logger.info("Running gate %r (%d checks)", effective_name, len(self._checks))
        self._results = []
        for entry in self._checks:
            self._results.append(self._execute_check(entry))
        self.report()
        exit_code = self.finalize()
        logger.info("Gate %r finished (exit_code=%d)", effective_name, exit_code)
        return GateReport(gate_name=effective_name, checks=tuple(self._results))

    # ── Internal helpers ─────────────────────────────────────────

    def _execute_check(self, entry: _CheckEntry) -> CheckResult:
        """Execute a single check with prerequisite, timeout, and retry handling."""
        if entry.prerequisite is not None:
            try:
                if not entry.prerequisite():
                    logger.info("Check %r skipped (prerequisite not met)", entry.name)
                    return CheckResult(
                        name=entry.name, status=CheckStatus.SKIP,
                        duration_ms=0.0, details="prerequisite not met",
                    )
            except Exception as exc:
                logger.warning("Prerequisite for %r raised: %s", entry.name, exc)
                return CheckResult(
                    name=entry.name, status=CheckStatus.SKIP,
                    duration_ms=0.0, details=f"prerequisite error: {exc}",
                )

        last_result: CheckResult | None = None
        attempts = 1 + entry.retries
        for attempt in range(1, attempts + 1):
            last_result = self._run_once(entry)
            if last_result.status != CheckStatus.FAIL:
                return last_result
            if attempt < attempts:
                logger.info(
                    "Check %r failed (attempt %d/%d), retrying in %.1fs",
                    entry.name, attempt, attempts, entry.retry_delay,
                )
                time.sleep(entry.retry_delay)

        assert last_result is not None  # noqa: S101
        return last_result

    def _run_once(self, entry: _CheckEntry) -> CheckResult:
        """Run a check function once with timeout enforcement."""
        start = time.monotonic()
        try:
            raw = entry.fn()
            elapsed_ms = (time.monotonic() - start) * 1000
            if elapsed_ms > entry.timeout * 1000:
                return CheckResult(
                    name=entry.name, status=CheckStatus.FAIL,
                    duration_ms=elapsed_ms,
                    details=f"exceeded timeout ({entry.timeout}s)",
                )
            if isinstance(raw, str):
                status, details = CheckStatus.PASS, raw
            elif raw:
                status, details = CheckStatus.PASS, ""
            else:
                status, details = CheckStatus.FAIL, "check returned False"
        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.warning("Check %r raised: %s", entry.name, exc)
            status, details = CheckStatus.FAIL, str(exc)
        return CheckResult(
            name=entry.name, status=status, duration_ms=elapsed_ms, details=details,
        )


# ── Shared file helpers ──────────────────────────────────────────


def read_file(path: Path) -> str:
    """Read *path* as UTF-8 text, returning empty string if missing."""
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def find_spec(
    specs_dir: Path, prefix: str, identifier: str, exts: tuple[str, ...],
) -> Path | None:
    """Find a spec file like ``{prefix}-{identifier}.{ext}`` in *specs_dir*."""
    for ext in exts:
        p = specs_dir / f"{prefix}-{identifier}.{ext}"
        if p.is_file():
            return p
    return None


def find_typed_spec(
    specs_dir: Path, prefix: str, identifier: str,
    variants: dict[str, str],
) -> tuple[str, Path] | None:
    """Like :func:`find_spec` but returns ``(kind, path)`` using a type mapping."""
    for ext, kind in variants.items():
        p = specs_dir / f"{prefix}-{identifier}{ext}"
        if p.exists():
            return kind, p
    return None


# ── Gate registry & orchestration ────────────────────────────────

_REPORT_DIR = ".dark-factory"
_REPORT_FILE = "gate-report.json"

GATE_REGISTRY: dict[str, str] = {
    "contract-validation": "factory.gates.contract_validation",
    "design-review": "factory.gates.design_review",
    "integration-test": "factory.gates.integration_test",
    "quality": "factory.gates.quality",
    "startup-health": "factory.gates.startup_health",
}


@dataclass(frozen=True, slots=True)
class GateInfo:
    """Metadata about a registered gate."""

    name: str
    module_name: str
    check_count: int


@dataclass(frozen=True, slots=True)
class UnifiedReport:
    """Aggregated results from running multiple gates."""

    gate_reports: tuple[GateReport, ...]
    overall_passed: bool = field(init=False)
    timestamp: float = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "overall_passed", all(gr.passed for gr in self.gate_reports))
        object.__setattr__(self, "timestamp", time.time())


def _load_gate_module(name: str) -> ModuleType | None:
    module_path = GATE_REGISTRY.get(name)
    if module_path is None:
        return None
    try:
        return importlib.import_module(module_path)
    except Exception:
        logger.warning("Failed to import gate module %s", module_path, exc_info=True)
        return None


def discover_gates(workspace: str | Path = ".", *, metrics_dir: str | Path | None = None) -> list[GateInfo]:
    """Return metadata for all registered gates."""
    ws = Path(workspace)
    gates: list[GateInfo] = []
    for gate_name, module_path in sorted(GATE_REGISTRY.items()):
        try:
            mod = importlib.import_module(module_path)
        except Exception:
            logger.warning("Failed to import gate module %s", module_path, exc_info=True)
            continue
        try:
            runner: GateRunner = mod.create_runner(ws, metrics_dir=metrics_dir)
            check_count = len(runner._checks)  # noqa: SLF001
        except Exception:
            logger.warning("Failed to create runner for %s", gate_name, exc_info=True)
            check_count = 0
        gates.append(GateInfo(name=gate_name, module_name=module_path, check_count=check_count))
    return gates


def run_all_gates(workspace: str | Path = ".", *, metrics_dir: str | Path | None = None) -> UnifiedReport:
    """Run every registered gate in sequence and produce a unified report."""
    ws = Path(workspace)
    md = Path(metrics_dir) if metrics_dir else Path(ws) / _REPORT_DIR
    reports: list[GateReport] = []
    for gate_name, module_path in sorted(GATE_REGISTRY.items()):
        try:
            mod = importlib.import_module(module_path)
            runner: GateRunner = mod.create_runner(ws, metrics_dir=md)
            report = runner.run()
            reports.append(report)
            logger.info("Gate %s: %s", gate_name, "PASSED" if report.passed else "FAILED")
        except Exception:
            logger.warning("Gate %s failed to run", gate_name, exc_info=True)
    reports.sort(key=lambda r: r.gate_name)
    unified = UnifiedReport(gate_reports=tuple(reports))
    write_gate_report(unified, report_dir=md)
    return unified


def run_gate_by_name(name: str, workspace: str | Path = ".", *, metrics_dir: str | Path | None = None) -> GateReport:
    """Run a single gate by name. Raises :class:`KeyError` if not found."""
    ws = Path(workspace)
    md = Path(metrics_dir) if metrics_dir else Path(ws) / _REPORT_DIR
    mod = _load_gate_module(name)
    if mod is None:
        msg = f"No gate found with name '{name}'"
        raise KeyError(msg)
    runner: GateRunner = mod.create_runner(ws, metrics_dir=md)
    report = runner.run()
    write_gate_report(UnifiedReport(gate_reports=(report,)), report_dir=md)
    return report


def write_gate_report(report: UnifiedReport, *, report_dir: str | Path | None = None) -> Path:
    """Write the unified gate report to ``gate-report.json``."""
    out_dir = Path(report_dir) if report_dir else Path(_REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _REPORT_FILE
    data: dict[str, Any] = {
        "timestamp": report.timestamp,
        "overall_passed": report.overall_passed,
        "gates": [
            {
                "gate_name": gr.gate_name, "passed": gr.passed,
                "checks": [
                    {"name": cr.name, "status": cr.status.value,
                     "duration_ms": round(cr.duration_ms, 2), "details": cr.details}
                    for cr in gr.checks
                ],
            }
            for gr in report.gate_reports
        ],
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("Gate report written to %s", path)
    return path


def load_gate_report(report_dir: str | Path | None = None) -> dict[str, Any] | None:
    """Load a previously-written gate report from disk."""
    path = (Path(report_dir) if report_dir else Path(_REPORT_DIR)) / _REPORT_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def format_gate_list(gates: list[GateInfo]) -> str:
    """Format a list of gates as a human-readable table."""
    if not gates:
        return "No gates registered."
    lines = ["Registered Gates", "=" * 50]
    for g in gates:
        lines.append(f"  {g.name:<25s}  {g.check_count} check(s)  [{g.module_name}]")
    lines.append("=" * 50)
    lines.append(f"Total: {len(gates)} gate(s)")
    return "\n".join(lines)


def format_unified_report(report: UnifiedReport) -> str:
    """Format a unified report as human-readable text."""
    lines: list[str] = ["Gate Report", "=" * 60]
    for gr in report.gate_reports:
        status = "PASSED" if gr.passed else "FAILED"
        lines.append(f"\n  Gate: {gr.gate_name} [{status}]")
        lines.append("  " + "-" * 40)
        for cr in gr.checks:
            line = f"    [{cr.status.value:4s}] {cr.name:<30s}  {cr.duration_ms:>8.1f}ms"
            if cr.details:
                line += f"  {cr.details[:60]}"
            lines.append(line)
    lines.append("")
    lines.append("=" * 60)
    lines.append(f"Overall: {'PASSED' if report.overall_passed else 'FAILED'}")
    return "\n".join(lines)
