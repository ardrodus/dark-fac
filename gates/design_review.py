"""Design review gate — delegates to :mod:`factory.gates.spec_gates`."""

from factory.gates.spec_gates import create_design_review_runner as create_runner
from factory.gates.spec_gates import run_design_review

GATE_NAME = "design-review"

__all__ = ["GATE_NAME", "create_runner", "run_design_review"]
