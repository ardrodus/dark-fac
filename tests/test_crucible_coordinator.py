"""Tests for crucible/coordinator.py — two-round pipeline coordination."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from dark_factory.crucible.coordinator import (
    CrucibleCoordinatorConfig,
    TwoRoundResult,
    _to_crucible_config,
    to_crucible_result,
)
from dark_factory.crucible.orchestrator import CrucibleVerdict


@dataclass
class FakeWorkspace:
    path: str
    repo: str = "owner/repo"


class TestToConfig:
    def test_converts_basic(self) -> None:
        cc = CrucibleCoordinatorConfig(
            build_timeout=100, health_timeout=30,
            test_timeout=200, project_name="test",
        )
        oc = _to_crucible_config(cc)
        assert oc.build_timeout == 100
        assert oc.project_name == "test"

    def test_timeout_override(self) -> None:
        cc = CrucibleCoordinatorConfig(test_timeout=200)
        oc = _to_crucible_config(cc, timeout=500)
        assert oc.test_timeout == 500


class TestTwoRoundResult:
    def test_default_values(self) -> None:
        r = TwoRoundResult(verdict=CrucibleVerdict.GO)
        assert r.verdict == CrucibleVerdict.GO
        assert r.round1_result is None
        assert r.round2_result is None
        assert r.phases == ()
        assert r.error == ""


class TestToCrucibleResult:
    def test_maps_go_verdict(self) -> None:
        tr = TwoRoundResult(verdict=CrucibleVerdict.GO)
        cr = to_crucible_result(tr)
        assert cr.verdict == CrucibleVerdict.GO
        assert cr.pass_count == 0
        assert cr.fail_count == 0

    def test_merges_round_results(self) -> None:
        from dark_factory.crucible.test_runner import RunResult, TestMode

        r1 = RunResult(
            mode=TestMode.SMOKE, verdict="GO",
            pass_count=4, fail_count=0, skip_count=0,
            duration_s=1.0,
        )
        r2 = RunResult(
            mode=TestMode.FULL, verdict="GO",
            pass_count=47, fail_count=0, skip_count=1,
            duration_s=5.0,
        )
        tr = TwoRoundResult(
            verdict=CrucibleVerdict.GO,
            round1_result=r1, round2_result=r2,
        )
        cr = to_crucible_result(tr)
        assert cr.pass_count == 51  # 4 + 47
        assert cr.skip_count == 1
        assert cr.fail_count == 0


class TestCrucibleCoordinatorConfig:
    def test_defaults(self) -> None:
        c = CrucibleCoordinatorConfig()
        assert c.build_timeout == 300
        assert c.smoke_timeout == 300
        assert c.regression_timeout == 600
        assert c.auto_graduate is True
        assert c.crucible_repo == ""

    def test_custom_values(self) -> None:
        c = CrucibleCoordinatorConfig(
            pr_number=42, pr_branch="feat/checkout",
            app_repo="owner/app", auto_graduate=False,
        )
        assert c.pr_number == 42
        assert c.pr_branch == "feat/checkout"
        assert c.auto_graduate is False
