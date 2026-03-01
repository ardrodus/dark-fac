"""Gate base framework — shared infrastructure for all validation gates.

Provides :class:`GateRunner`, the single entry-point for registering and
executing named checks with standard output formatting, timeout handling,
transient-failure retry, prerequisite skipping, and metrics persistence.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

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
