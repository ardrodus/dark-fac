"""Tests for crucible/graduation.py — test graduation (bill becomes law)."""
from __future__ import annotations

from pathlib import Path

import pytest

from dark_factory.crucible.graduation import (
    GraduationResult,
    _check_conflicts,
    _rename_for_graduation,
    _resolve_conflict,
    graduate_tests,
)
from dark_factory.crucible.scenario_gen import ScenarioGenResult, ScenarioTest


class TestRenameForGraduation:
    def test_removes_pr_prefix(self) -> None:
        assert _rename_for_graduation("tests/pr-42-checkout.spec.ts", 42) == "tests/checkout.spec.ts"

    def test_removes_generic_prefix(self) -> None:
        assert _rename_for_graduation("tests/pr-99-api.spec.ts", 99) == "tests/api.spec.ts"

    def test_no_prefix_unchanged(self) -> None:
        result = _rename_for_graduation("tests/checkout.spec.ts", 42)
        assert result == "tests/checkout.spec.ts"

    def test_python_file(self) -> None:
        assert _rename_for_graduation("tests/pr-5-test_auth.py", 5) == "tests/test_auth.py"


class TestResolveConflict:
    def test_adds_v2_suffix(self) -> None:
        assert _resolve_conflict("tests/checkout.spec.ts") == "tests/checkout-v2.spec.ts"

    def test_multi_extension(self) -> None:
        assert _resolve_conflict("tests/api.test.js") == "tests/api-v2.test.js"

    def test_python_file(self) -> None:
        assert _resolve_conflict("tests/test_auth.py") == "tests/test_auth-v2.py"


class TestCheckConflicts:
    def test_detects_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "checkout.spec.ts").touch()
        conflicts = _check_conflicts(tmp_path, ["tests/checkout.spec.ts"])
        assert len(conflicts) == 1

    def test_no_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        conflicts = _check_conflicts(tmp_path, ["tests/new-test.spec.ts"])
        assert len(conflicts) == 0


class TestGraduateTests:
    def _scenario_result(self) -> ScenarioGenResult:
        return ScenarioGenResult(
            tests=(
                ScenarioTest(
                    name="checkout",
                    file_path="tests/pr-42-checkout.spec.ts",
                    test_code="test('checkout', () => { expect(true).toBe(true); });",
                    framework="playwright",
                    category="smoke",
                ),
            ),
            pr_number=42,
            pr_diff_summary="1 file changed",
            frameworks_used=("playwright",),
        )

    def test_empty_scenario_returns_error(self, tmp_path: Path) -> None:
        empty = ScenarioGenResult(
            tests=(), pr_number=1, pr_diff_summary="", frameworks_used=(),
        )
        result = graduate_tests(tmp_path, empty, "owner/repo", 1)
        assert not result.graduated
        assert "No tests" in result.error

    def test_graduates_and_renames(self, tmp_path: Path) -> None:
        cruc = tmp_path / "crucible"
        cruc.mkdir()
        (cruc / ".git").mkdir()  # fake git repo
        (cruc / "tests").mkdir()
        # Write the pr-specific test file
        test_file = cruc / "tests" / "pr-42-checkout.spec.ts"
        test_file.write_text("test code", encoding="utf-8")

        calls: list[tuple[list[str], str]] = []

        class FakeResult:
            returncode = 0
            stdout = "https://github.com/owner/repo-crucible/pull/1"
            stderr = ""

        def fake_git(args: list[str], cwd: str = "") -> FakeResult:
            calls.append((args, cwd))
            return FakeResult()

        def fake_gh(args: list[str], cwd: str = "") -> FakeResult:
            calls.append((args, cwd))
            return FakeResult()

        result = graduate_tests(
            cruc, self._scenario_result(), "owner/repo", 42,
            git_fn=fake_git, gh_fn=fake_gh,
        )
        assert result.graduated is True
        assert "pull/1" in result.pr_url
        assert "checkout.spec.ts" in result.files_added[0]
        # Original pr-42 file should be gone
        assert not test_file.exists()
        # Graduated file should exist
        assert (cruc / "tests" / "checkout.spec.ts").is_file()

    def test_conflict_resolution(self, tmp_path: Path) -> None:
        cruc = tmp_path / "crucible"
        cruc.mkdir()
        (cruc / ".git").mkdir()
        (cruc / "tests").mkdir()
        # Create existing file that would conflict
        (cruc / "tests" / "checkout.spec.ts").write_text("existing", encoding="utf-8")
        # Create PR-specific file
        (cruc / "tests" / "pr-42-checkout.spec.ts").write_text("new test", encoding="utf-8")

        class FakeResult:
            returncode = 0
            stdout = "https://github.com/o/r/pull/2"
            stderr = ""

        result = graduate_tests(
            cruc, self._scenario_result(), "o/r", 42,
            git_fn=lambda *a, **kw: FakeResult(),
            gh_fn=lambda *a, **kw: FakeResult(),
        )
        assert result.graduated is True
        # Should use -v2 suffix due to conflict
        assert any("-v2" in f for f in result.files_added)
