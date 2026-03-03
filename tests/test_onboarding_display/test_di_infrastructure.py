"""Story 1: Validate DI test infrastructure (OnboardingDisplayConfig, MockPresenter, fixtures).

Acceptance criteria:
- MockPresenter implements all 9 OnboardingPresenter methods and captures calls
- captured_output fixture provides a list[str] for writer injection
- display_config fixture creates OnboardingDisplayConfig with proper wiring
- mock_gh_cmd fixture patches gh_cmd() to return success
- mock_input fixture patches input() with predetermined responses
- All fixtures are importable from conftest
"""

from __future__ import annotations

from tests.test_onboarding_display.conftest import (
    MockPresenter,
    OnboardingDisplayConfig,
)


class TestMockPresenter:
    """MockPresenter implements all 9 methods and captures calls as tuples."""

    def test_captures_show_banner(self) -> None:
        mp = MockPresenter()
        mp.show_banner()
        assert len(mp.calls) == 1
        assert mp.calls[0] == ("show_banner", (), {})

    def test_captures_phase_header(self) -> None:
        mp = MockPresenter()
        mp.phase_header(1, 14, "Platform", pillar="sentinel")
        assert mp.calls[0] == ("phase_header", (1, 14, "Platform"), {"pillar": "sentinel"})

    def test_captures_status_line(self) -> None:
        mp = MockPresenter()
        mp.status_line("Connected", level="success")
        assert mp.calls[0] == ("status_line", ("Connected",), {"level": "success"})

    def test_captures_dep_status(self) -> None:
        mp = MockPresenter()
        mp.dep_status("git", True)
        assert mp.calls[0] == ("dep_status", ("git", True), {})

    def test_captures_completion_panel(self) -> None:
        mp = MockPresenter()
        mp.completion_panel("acme/app", "web", 17)
        assert mp.calls[0] == ("completion_panel", ("acme/app", "web", 17), {})

    def test_captures_error(self) -> None:
        mp = MockPresenter()
        mp.error("Failed", hint="Check auth")
        assert mp.calls[0] == ("error", ("Failed",), {"hint": "Check auth"})

    def test_captures_menu(self) -> None:
        mp = MockPresenter()
        mp.menu([("1", "CLI")], recommended="1")
        assert mp.calls[0] == ("menu", ([("1", "CLI")],), {"recommended": "1"})

    def test_captures_stage_result(self) -> None:
        mp = MockPresenter()
        mp.stage_result("build", "passed", "ok")
        assert mp.calls[0] == ("stage_result", ("build", "passed", "ok"), {})

    def test_captures_verdict_tag(self) -> None:
        mp = MockPresenter()
        result = mp.verdict_tag("PASS")
        assert mp.calls[0] == ("verdict_tag", ("PASS",), {})
        assert result == "[PASS]"

    def test_all_nine_methods_exist(self) -> None:
        """All 9 OnboardingPresenter methods are implemented."""
        mp = MockPresenter()
        methods = [
            "show_banner", "phase_header", "status_line", "dep_status",
            "completion_panel", "error", "menu", "stage_result", "verdict_tag",
        ]
        for m in methods:
            assert hasattr(mp, m), f"MockPresenter missing method: {m}"
            assert callable(getattr(mp, m))

    def test_get_calls_filters_by_method(self) -> None:
        mp = MockPresenter()
        mp.show_banner()
        mp.status_line("hello")
        mp.show_banner()
        banner_calls = mp.get_calls("show_banner")
        assert len(banner_calls) == 2
        status_calls = mp.get_calls("status_line")
        assert len(status_calls) == 1


class TestCapturedOutputFixture:
    """captured_output provides a list[str] for writer injection."""

    def test_is_list(self, captured_output: list[str]) -> None:
        assert isinstance(captured_output, list)
        assert len(captured_output) == 0

    def test_append_captures_strings(self, captured_output: list[str]) -> None:
        captured_output.append("hello\n")
        captured_output.append("world\n")
        assert captured_output == ["hello\n", "world\n"]


class TestDisplayConfigFixture:
    """display_config wires writer and presenter correctly."""

    def test_writer_is_list_append(self, display_config, captured_output) -> None:
        display_config.writer("test output\n")
        assert captured_output == ["test output\n"]

    def test_presenter_is_mock(self, display_config, mock_presenter) -> None:
        assert display_config.presenter is mock_presenter

    def test_use_rich_defaults_true(self, display_config) -> None:
        assert display_config.use_rich is True


class TestMockGhCmd:
    """mock_gh_cmd patches gh_cmd to return success."""

    def test_returns_success(self, mock_gh_cmd) -> None:
        from dark_factory.integrations.shell import gh

        result = gh(["auth", "status"])
        assert result.returncode == 0

    def test_no_network_calls(self, mock_gh_cmd) -> None:
        from dark_factory.integrations.shell import gh

        gh(["repo", "view", "acme/app"])
        mock_gh_cmd.assert_called_once()


class TestMockInput:
    """mock_input patches input() with predetermined responses."""

    def test_returns_predetermined_responses(self, mock_input) -> None:
        mock_input.extend(["yes", "no"])
        assert input("First? ") == "yes"
        assert input("Second? ") == "no"

    def test_returns_empty_when_exhausted(self, mock_input) -> None:
        assert input("Any? ") == ""
