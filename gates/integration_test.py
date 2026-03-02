"""Integration test gate — delegates to :mod:`factory.gates.spec_gates`."""

from dark_factory.gates.spec_gates import (
    collect_existing_tests,
    collect_story_artifacts,
    run_integration_test_gate,
)
from dark_factory.gates.spec_gates import (
    create_integration_test_runner as create_runner,
)

GATE_NAME = "integration-test"

__all__ = [
    "GATE_NAME",
    "collect_existing_tests",
    "collect_story_artifacts",
    "create_runner",
    "run_integration_test_gate",
]
