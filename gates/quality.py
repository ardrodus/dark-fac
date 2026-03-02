"""File-type-aware quality gate runner.

Selects the appropriate linting and type-checking tools based on file extension:

- **Python** (``.py``): ``ruff check`` + ``mypy --strict``
- **Bash** (``.sh``): ``shellcheck``

Mixed changesets run both gate sets and merge the results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from dark_factory.gates.framework import GateRunner
from dark_factory.integrations.shell import CommandResult, run_command

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


class FileKind(Enum):
    """Recognised file types for quality gates."""

    PYTHON = "python"
    BASH = "bash"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of a single quality gate check."""

    gate: str
    passed: bool
    output: str
    returncode: int


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Aggregated results from all quality gates."""

    results: tuple[GateResult, ...]
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", all(r.passed for r in self.results))


def classify_file(path: str | Path) -> FileKind:
    """Return the :class:`FileKind` for *path* based on its extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return FileKind.PYTHON
    if suffix == ".sh":
        return FileKind.BASH
    return FileKind.UNKNOWN


def classify_changeset(paths: Sequence[str | Path]) -> set[FileKind]:
    """Return the set of :class:`FileKind` values present in *paths*."""
    return {classify_file(p) for p in paths} - {FileKind.UNKNOWN}


# ── Individual gates ──────────────────────────────────────────────


def _run_gate(name: str, cmd: list[str], *, cwd: str | None = None, timeout: float = 120) -> GateResult:
    """Run a single gate command and return a :class:`GateResult`."""
    logger.info("Running gate %r: %s", name, " ".join(cmd))
    result: CommandResult = run_command(cmd, timeout=timeout, cwd=cwd)
    passed = result.returncode == 0
    output = (result.stdout + result.stderr).strip()
    logger.info("Gate %r %s (rc=%d)", name, "PASSED" if passed else "FAILED", result.returncode)
    return GateResult(gate=name, passed=passed, output=output, returncode=result.returncode)


def gate_ruff(targets: Sequence[str], *, cwd: str | None = None) -> GateResult:
    """Run ``ruff check`` on the given targets."""
    cmd = ["ruff", "check", *targets]
    return _run_gate("ruff-check", cmd, cwd=cwd)


def gate_mypy(targets: Sequence[str], *, cwd: str | None = None) -> GateResult:
    """Run ``mypy --strict`` on the given targets."""
    cmd = ["mypy", "--strict", *targets]
    return _run_gate("mypy-strict", cmd, cwd=cwd)


def gate_shellcheck(targets: Sequence[str], *, cwd: str | None = None) -> GateResult:
    """Run ``shellcheck`` on the given targets."""
    cmd = ["shellcheck", *targets]
    return _run_gate("shellcheck", cmd, cwd=cwd)


def gate_pytest(test_dir: str = "tests/", *, cwd: str | None = None) -> GateResult:
    """Run ``pytest`` on the test directory."""
    cmd = ["pytest", test_dir, "-v", "--tb=short"]
    return _run_gate("pytest", cmd, cwd=cwd, timeout=300)


# ── Discovery interface ───────────────────────────────────────────

GATE_NAME = "quality"


def _tool_check(cmd: list[str], label: str, *, timeout: float = 120, cwd: str | None = None) -> bool | str:
    """Run *cmd* and return a pass message or raise on failure."""
    result = run_command(cmd, timeout=timeout, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed: {(result.stdout + result.stderr).strip()[:200]}")
    return f"{label} passed"


def create_runner(
    workspace: str | Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Create a configured (but not executed) quality gate runner."""
    cwd = str(workspace)
    runner = GateRunner(GATE_NAME, metrics_dir=metrics_dir)
    runner.register_check(
        "ruff-check", lambda: _tool_check(["ruff", "check", "factory/"], "ruff check", cwd=cwd))
    runner.register_check(
        "mypy-strict",
        lambda: _tool_check(["mypy", "--strict", "factory/"], "mypy --strict", cwd=cwd),
    )
    runner.register_check(
        "pytest",
        lambda: _tool_check(["pytest", "tests/", "-v", "--tb=short"], "pytest", timeout=300, cwd=cwd),
    )
    return runner


# ── Orchestration ─────────────────────────────────────────────────


def run_quality_gates(
    changed_files: Sequence[str | Path],
    *,
    cwd: str | None = None,
    python_targets: Sequence[str] | None = None,
    bash_targets: Sequence[str] | None = None,
    run_tests: bool = True,
) -> QualityReport:
    """Run the appropriate quality gates for *changed_files*.

    Parameters
    ----------
    changed_files:
        List of file paths that were changed.
    cwd:
        Working directory for subprocess invocations.
    python_targets:
        Override Python lint targets (default: ``["factory/"]``).
    bash_targets:
        Override bash lint targets (default: the ``.sh`` files in *changed_files*).
    run_tests:
        Whether to include ``pytest`` in the gate set.
    """
    kinds = classify_changeset(changed_files)
    results: list[GateResult] = []

    if FileKind.PYTHON in kinds:
        py_targets = list(python_targets) if python_targets else ["factory/"]
        results.append(gate_ruff(py_targets, cwd=cwd))
        results.append(gate_mypy(py_targets, cwd=cwd))

    if FileKind.BASH in kinds:
        sh_targets = (
            list(bash_targets)
            if bash_targets
            else [str(p) for p in changed_files if classify_file(p) == FileKind.BASH]
        )
        if sh_targets:
            results.append(gate_shellcheck(sh_targets, cwd=cwd))

    if run_tests and FileKind.PYTHON in kinds:
        results.append(gate_pytest(cwd=cwd))

    if not results:
        results.append(GateResult(gate="no-op", passed=True, output="No recognised files to check.", returncode=0))

    return QualityReport(results=tuple(results))
