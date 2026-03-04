"""Crucible data types — verdict, test result, phase metrics."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CrucibleVerdict(Enum):
    GO = "GO"
    NO_GO = "NO_GO"
    NEEDS_LIVE = "NEEDS_LIVE"


@dataclass(frozen=True, slots=True)
class TestResult:
    name: str
    status: str
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class PhaseMetrics:
    phase: str
    duration_s: float
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CrucibleResult:
    """Full outcome of a Crucible test run."""

    verdict: CrucibleVerdict
    test_results: tuple[TestResult, ...]
    screenshots: tuple[str, ...]
    logs: str
    phases: tuple[PhaseMetrics, ...]
    pass_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    duration_s: float = 0.0
    error: str = ""
