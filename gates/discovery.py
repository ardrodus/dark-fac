"""Gate discovery and unified runner for dark-factory gates.

Auto-discovers gate modules in the ``factory.gates`` package via
``importlib`` and ``pkgutil``.  A module is recognised as a gate if it
exposes:

- ``GATE_NAME: str`` — human-readable name for the gate
- ``create_runner(workspace, *, metrics_dir=None) -> GateRunner`` — factory
  that returns a configured (but not yet executed) :class:`GateRunner`

Usage::

    gates = discover_gates()                     # find all gate modules
    report = run_all_gates(workspace=".")        # run every gate
    report = run_gate_by_name("quality", ".")    # run a single gate
"""

from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import factory.gates as _gates_pkg

if TYPE_CHECKING:
    from types import ModuleType

    from factory.gates.framework import GateReport, GateRunner

logger = logging.getLogger(__name__)

_SKIP_MODULES = frozenset({"framework", "discovery"})
_REPORT_DIR = ".dark-factory"
_REPORT_FILE = "gate-report.json"


@dataclass(frozen=True, slots=True)
class GateInfo:
    """Metadata about a discovered gate."""

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
        object.__setattr__(
            self, "overall_passed",
            all(gr.passed for gr in self.gate_reports),
        )
        object.__setattr__(self, "timestamp", time.time())


# ── Discovery ────────────────────────────────────────────────────


def _is_gate_module(mod: ModuleType) -> bool:
    """Return True if *mod* follows the gate discovery protocol."""
    return (
        hasattr(mod, "GATE_NAME")
        and isinstance(mod.GATE_NAME, str)
        and callable(getattr(mod, "create_runner", None))
    )


def discover_gates(
    workspace: str | Path = ".",
    *,
    metrics_dir: str | Path | None = None,
) -> list[GateInfo]:
    """Find all gate modules in ``factory.gates`` and return their metadata.

    Each discovered module is imported and its ``create_runner`` is called
    to determine the number of registered checks.
    """
    ws = Path(workspace)
    gates: list[GateInfo] = []
    pkg_path = _gates_pkg.__path__

    for _importer, mod_name, is_pkg in pkgutil.iter_modules(pkg_path):
        if mod_name in _SKIP_MODULES or is_pkg:
            continue
        full_name = f"factory.gates.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception:
            logger.warning("Failed to import gate module %s", full_name, exc_info=True)
            continue

        if not _is_gate_module(mod):
            continue

        try:
            runner: GateRunner = mod.create_runner(ws, metrics_dir=metrics_dir)
            check_count = len(runner._checks)  # noqa: SLF001
        except Exception:
            logger.warning("Failed to create runner for %s", mod.GATE_NAME, exc_info=True)
            check_count = 0

        gates.append(GateInfo(
            name=mod.GATE_NAME,
            module_name=full_name,
            check_count=check_count,
        ))

    gates.sort(key=lambda g: g.name)
    return gates


def _load_gate_module(name: str) -> ModuleType | None:
    """Find and return the gate module matching *name*."""
    pkg_path = _gates_pkg.__path__
    for _importer, mod_name, is_pkg in pkgutil.iter_modules(pkg_path):
        if mod_name in _SKIP_MODULES or is_pkg:
            continue
        full_name = f"factory.gates.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception:
            continue
        if _is_gate_module(mod) and name == mod.GATE_NAME:
            return mod
    return None


# ── Execution ────────────────────────────────────────────────────


def run_all_gates(
    workspace: str | Path = ".",
    *,
    metrics_dir: str | Path | None = None,
) -> UnifiedReport:
    """Run every discovered gate in sequence and produce a unified report."""
    ws = Path(workspace)
    md = Path(metrics_dir) if metrics_dir else Path(ws) / _REPORT_DIR
    reports: list[GateReport] = []

    pkg_path = _gates_pkg.__path__
    for _importer, mod_name, is_pkg in pkgutil.iter_modules(pkg_path):
        if mod_name in _SKIP_MODULES or is_pkg:
            continue
        full_name = f"factory.gates.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception:
            logger.warning("Failed to import %s", full_name, exc_info=True)
            continue
        if not _is_gate_module(mod):
            continue
        try:
            runner: GateRunner = mod.create_runner(ws, metrics_dir=md)
            report = runner.run()
            reports.append(report)
            logger.info("Gate %s: %s", mod.GATE_NAME, "PASSED" if report.passed else "FAILED")
        except Exception:
            logger.warning("Gate %s failed to run", mod.GATE_NAME, exc_info=True)

    reports.sort(key=lambda r: r.gate_name)
    unified = UnifiedReport(gate_reports=tuple(reports))
    write_gate_report(unified, report_dir=md)
    return unified


def run_gate_by_name(
    name: str,
    workspace: str | Path = ".",
    *,
    metrics_dir: str | Path | None = None,
) -> GateReport:
    """Run a single gate by name and return its report.

    Raises
    ------
    KeyError
        If no gate with the given name is found.
    """
    ws = Path(workspace)
    md = Path(metrics_dir) if metrics_dir else Path(ws) / _REPORT_DIR
    mod = _load_gate_module(name)
    if mod is None:
        msg = f"No gate found with name '{name}'"
        raise KeyError(msg)

    runner: GateRunner = mod.create_runner(ws, metrics_dir=md)
    report = runner.run()
    unified = UnifiedReport(gate_reports=(report,))
    write_gate_report(unified, report_dir=md)
    return report


# ── Reporting ────────────────────────────────────────────────────


def write_gate_report(
    report: UnifiedReport,
    *,
    report_dir: str | Path | None = None,
) -> Path:
    """Write the unified gate report to ``gate-report.json``.

    Returns the path to the written file.
    """
    out_dir = Path(report_dir) if report_dir else Path(_REPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _REPORT_FILE

    data: dict[str, Any] = {
        "timestamp": report.timestamp,
        "overall_passed": report.overall_passed,
        "gates": [
            {
                "gate_name": gr.gate_name,
                "passed": gr.passed,
                "checks": [
                    {
                        "name": cr.name,
                        "status": cr.status.value,
                        "duration_ms": round(cr.duration_ms, 2),
                        "details": cr.details,
                    }
                    for cr in gr.checks
                ],
            }
            for gr in report.gate_reports
        ],
    }

    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("Gate report written to %s", path)
    return path


def format_gate_list(gates: list[GateInfo]) -> str:
    """Format a list of discovered gates as a human-readable table."""
    if not gates:
        return "No gates discovered."
    lines: list[str] = ["Registered Gates", "=" * 50]
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
    overall = "PASSED" if report.overall_passed else "FAILED"
    lines.append(f"Overall: {overall}")
    return "\n".join(lines)


def load_gate_report(report_dir: str | Path | None = None) -> dict[str, Any] | None:
    """Load a previously-written gate report from disk.

    Returns ``None`` if the file does not exist or is invalid.
    """
    out_dir = Path(report_dir) if report_dir else Path(_REPORT_DIR)
    path = out_dir / _REPORT_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None
