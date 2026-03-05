"""Quality gates and gate runner infrastructure.

Security gates are handled by the sentinel DOT pipeline (pipelines/sentinel.dot).
This package provides the GateRunner base class and quality gates used by
the pipeline runner and self-forge validation.
"""

from dark_factory.gates.framework import (
    CheckResult,
    CheckStatus,
    CheckTimeoutError,
    GateInfo,
    GateReport,
    GateRunner,
    UnifiedReport,
    discover_gates,
    format_gate_list,
    format_unified_report,
    load_gate_report,
    run_all_gates,
    run_gate_by_name,
    write_gate_report,
)

__all__ = [
    "CheckResult",
    "CheckStatus",
    "CheckTimeoutError",
    "GateInfo",
    "GateReport",
    "GateRunner",
    "UnifiedReport",
    "discover_gates",
    "format_gate_list",
    "format_unified_report",
    "load_gate_report",
    "run_all_gates",
    "run_gate_by_name",
    "write_gate_report",
]
