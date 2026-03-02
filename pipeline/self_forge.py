"""Self-forge and self-crucible validation (US-811).

When the factory processes its own repository, extra validation gates
apply before changes can be merged.  Ports ``self-forge.sh`` /
``self-crucible.sh`` to Python.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from dark_factory.gates.quality import gate_mypy, gate_pytest, gate_ruff

if TYPE_CHECKING:
    from dark_factory.core.config_manager import ConfigData
    from dark_factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)

# Marker files that identify a checkout as the factory's own repo.
_FACTORY_MARKERS: tuple[tuple[str, ...], ...] = (
    ("__init__.py",),
    ("cli", "parser.py"),
    ("cli", "dispatch.py"),
    ("gates", "framework.py"),
    ("__main__.py",),
)

# The four validation layers (matching bash self-crucible.sh).
_LAYER_LINT = "lint"
_LAYER_TESTS = "tests"
_LAYER_PIPELINE_SIM = "pipeline_simulation"


@dataclass(frozen=True, slots=True)
class LayerResult:
    """Outcome of a single self-crucible validation layer."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class SelfValidationResult:
    """Aggregated result of the 4-layer self-crucible suite."""

    layers: tuple[LayerResult, ...]
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", all(lr.passed for lr in self.layers))


# ── Public API ───────────────────────────────────────────────────


def is_self_repo(config: ConfigData) -> bool:
    """Return *True* if the configured repo is the factory itself.

    Detection checks (any match → True):
    1. ``config.data["self_onboarded"]`` flag set by self-onboarding.
    2. Well-known marker files present under the factory package root.
    """
    if config.data.get("self_onboarded"):
        return True
    root = Path(config.data.get("project", {}).get("root", ""))
    if not root.is_dir():
        return False
    return all((root / Path(*parts)).is_file() for parts in _FACTORY_MARKERS)


def run_self_validation(workspace: Workspace) -> SelfValidationResult:
    """Run the 3-layer self-crucible validation suite on *workspace*.

    Layers
    ------
    1. **Lint** — ``ruff check factory/``
    2. **Tests** — ``pytest tests/ -v --tb=short``
    3. **Pipeline simulation** — gate discovery (all gates loadable)
    """
    cwd = workspace.path
    layers: list[LayerResult] = [
        _layer_lint(cwd),
        _layer_tests(cwd),
        _layer_pipeline_sim(cwd),
    ]
    result = SelfValidationResult(layers=tuple(layers))
    logger.info("Self-validation %s (%d/%d layers passed)",
                "PASSED" if result.passed else "FAILED",
                sum(lr.passed for lr in layers), len(layers))
    return result


# ── Layer implementations ────────────────────────────────────────


def _layer_lint(cwd: str) -> LayerResult:
    ruff = gate_ruff(["factory/"], cwd=cwd)
    mypy = gate_mypy(["factory/"], cwd=cwd)
    passed = ruff.passed and mypy.passed
    parts = [f"ruff={'OK' if ruff.passed else 'FAIL'}",
             f"mypy={'OK' if mypy.passed else 'FAIL'}"]
    return LayerResult(name=_LAYER_LINT, passed=passed, detail=", ".join(parts))


def _layer_tests(cwd: str) -> LayerResult:
    r = gate_pytest(cwd=cwd)
    return LayerResult(name=_LAYER_TESTS, passed=r.passed, detail=r.output[:200])


def _layer_pipeline_sim(cwd: str) -> LayerResult:
    """Verify all registered gates are discoverable and loadable."""
    try:
        from dark_factory.gates import discover_gates  # noqa: PLC0415
        gates = discover_gates()
        return LayerResult(
            name=_LAYER_PIPELINE_SIM, passed=len(gates) > 0,
            detail=f"{len(gates)} gate(s) discovered",
        )
    except Exception as exc:
        return LayerResult(name=_LAYER_PIPELINE_SIM, passed=False, detail=str(exc))


