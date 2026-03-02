"""Contract validation gate — delegates to :mod:`factory.gates.spec_gates`."""

from dark_factory.gates.spec_gates import create_contract_validation_runner as create_runner
from dark_factory.gates.spec_gates import run_contract_validation

GATE_NAME = "contract-validation"

__all__ = ["GATE_NAME", "create_runner", "run_contract_validation"]
