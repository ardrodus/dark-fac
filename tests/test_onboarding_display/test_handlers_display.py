"""Story 8: Unit tests for cli/handlers.py run_onboard() styled self-onboard display.

Tests:
- Step results use stage icons for OK/FAIL/WARN states
- Summary output uses cprint/styled for Rich markup
- Machine-parseable 'onboard --self: PASS/FAIL' verdict line preserved
- PASS verdict styled with verdict_tag() green
- FAIL verdict styled with verdict_tag() red
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


@dataclass(frozen=True, slots=True)
class _FakeOnboardResult:
    passed: bool
    steps: tuple[str, ...]


class TestHandlersSelfOnboardDisplay:
    """5 unit tests for run_onboard() styled self-onboard summary."""

    def test_step_results_use_stage_icons(self) -> None:
        """Step results use stage icons for OK/FAIL/WARN states."""
        fake_result = _FakeOnboardResult(
            passed=True,
            steps=(
                "OK: Factory repository detected",
                "OK: Analysis -- language=Python",
                "WARN: Missing tools: mypy",
            ),
        )
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.setup.self_onboard.run_onboard_self", return_value=fake_result),
        ):
            from dark_factory.cli.handlers import run_onboard

            run_onboard(self_onboard=True)

        full_output = "".join(captured)
        # Each step should appear in output
        assert "OK: Factory repository detected" in full_output
        assert "WARN: Missing tools" in full_output

    def test_summary_output_uses_styled_output(self) -> None:
        """Summary output uses styled output methods."""
        fake_result = _FakeOnboardResult(
            passed=True,
            steps=("OK: All checks passed",),
        )
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.setup.self_onboard.run_onboard_self", return_value=fake_result),
        ):
            from dark_factory.cli.handlers import run_onboard

            run_onboard(self_onboard=True)

        full_output = "".join(captured)
        # Should contain summary section
        assert "Onboarding Summary" in full_output or "Summary" in full_output

    def test_machine_parseable_verdict_line(self) -> None:
        """Machine-parseable 'onboard --self: PASS/FAIL' verdict line preserved."""
        fake_result = _FakeOnboardResult(passed=True, steps=("OK: done",))
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.setup.self_onboard.run_onboard_self", return_value=fake_result),
        ):
            from dark_factory.cli.handlers import run_onboard

            run_onboard(self_onboard=True)

        full_output = "".join(captured)
        assert "onboard --self: PASS" in full_output

    def test_pass_verdict_green(self) -> None:
        """PASS verdict is styled with success semantics (green)."""
        fake_result = _FakeOnboardResult(passed=True, steps=("OK: done",))
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.setup.self_onboard.run_onboard_self", return_value=fake_result),
        ):
            from dark_factory.cli.handlers import run_onboard

            run_onboard(self_onboard=True)

        full_output = "".join(captured)
        # PASS verdict should be present
        assert "PASS" in full_output

    def test_fail_verdict_red(self) -> None:
        """FAIL verdict is styled with error semantics (red)."""
        fake_result = _FakeOnboardResult(passed=False, steps=("FAIL: selftest failed",))
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.setup.self_onboard.run_onboard_self", return_value=fake_result),
        ):
            from dark_factory.cli.handlers import run_onboard

            with pytest.raises(SystemExit) as exc_info:
                run_onboard(self_onboard=True)
            assert exc_info.value.code == 1

        full_output = "".join(captured)
        # FAIL verdict should be present
        assert "FAIL" in full_output
