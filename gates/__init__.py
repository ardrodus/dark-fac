"""Quality gate checks — lint, type-check, and test runners.

Gate enumeration uses a static registry instead of dynamic discovery.
"""

from factory.gates.contract_validation import run_contract_validation
from factory.gates.design_review import run_design_review
from factory.gates.framework import (
    GATE_REGISTRY,
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
from factory.gates.integration_test import run_integration_test_gate
from factory.gates.startup_health import run_startup_health

__all__ = [
    "CheckResult",
    "CheckStatus",
    "CheckTimeoutError",
    "GATE_REGISTRY",
    "GateInfo",
    "GateReport",
    "GateRunner",
    "UnifiedReport",
    "discover_gates",
    "format_gate_list",
    "format_unified_report",
    "load_gate_report",
    "run_all_gates",
    "run_contract_validation",
    "run_design_review",
    "run_gate_by_name",
    "run_integration_test_gate",
    "run_startup_health",
    "write_gate_report",
]
