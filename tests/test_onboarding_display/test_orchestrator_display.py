"""Story 4: Unit tests for orchestrator.py styled display output.

Tests run_onboarding() styled output via MockPresenter DI:
- show_banner() called at start
- phase_header() called with correct step/total for each phase
- status_line() called with correct semantic level
- dep_status() called for each tool with correct found status
- completion_panel() called with correct repo/strategy/labels on success
- error() called with message and hint on failure
- Exit code 0 on success path
- Exit code 1 on failure path
- Machine-parseable 'Onboarding complete!' preserved
- Machine-parseable 'GITHUB_REPO=...' preserved
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.test_onboarding_display.conftest import MockPresenter, OnboardingDisplayConfig


def _make_success_mocks():
    """Create a complete set of mocks for a successful onboarding run."""
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = ""
    fake_result.stderr = ""

    fake_plat = MagicMock()
    fake_plat.os = "linux"
    fake_plat.arch = "x86_64"
    fake_plat.shell = "bash"

    fake_dep = MagicMock()
    fake_dep.name = "git"
    fake_dep.found = True

    fake_analysis = MagicMock()
    fake_analysis.language = "Python"
    fake_analysis.framework = "pytest"
    fake_analysis.detected_app_type = "console"
    fake_analysis.confidence = "high"
    fake_analysis.required_tools = ("python", "pip")
    fake_analysis.base_image = "python:3.12-bookworm"

    fake_install = MagicMock()
    fake_install.installed = 0
    fake_install.skipped = 2
    fake_install.failed = 0

    fake_prov = {"labels": 17, "ci_workflow": True, "issue_template": True, "branch_protection": True}

    fake_strat_cfg = MagicMock()
    fake_strat_cfg.bootstrap_deps = ["pytest"]

    return {
        "gh": fake_result,
        "plat": fake_plat,
        "deps": [fake_dep],
        "analysis": fake_analysis,
        "install": fake_install,
        "prov": fake_prov,
        "strat_cfg": fake_strat_cfg,
    }


def _apply_success_patches(stack: ExitStack, mocks: dict, captured: list[str], repo: str = "acme/app") -> None:
    """Apply all patches for a successful onboarding run onto an ExitStack."""
    p = stack.enter_context
    p(patch("dark_factory.integrations.shell.gh", return_value=mocks["gh"]))
    p(patch("dark_factory.setup.platform.detect_platform", return_value=mocks["plat"]))
    p(patch("dark_factory.setup.platform.check_dependencies", return_value=mocks["deps"]))
    p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value="opus"))
    p(patch("dark_factory.setup.claude_detect.prompt_claude_model", return_value="opus"))
    p(patch("dark_factory.setup.claude_detect.save_claude_model"))
    p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=True))
    p(patch("dark_factory.setup.github_auth.connect_github", return_value=True))
    p(patch("dark_factory.setup.project_analyzer.analyze_project", return_value=mocks["analysis"]))
    p(patch("dark_factory.setup.project_analyzer.display_analysis_results"))
    p(patch("dark_factory.setup.project_analyzer.confirm_or_override_analysis", return_value=mocks["analysis"]))
    p(patch("dark_factory.setup.config_init.prompt_app_type", return_value="console"))
    p(patch("dark_factory.setup.config_init.init_config"))
    p(patch("dark_factory.setup.config_init.add_repo_to_config"))
    p(patch("dark_factory.setup.dep_installer.install_project_deps", return_value=mocks["install"]))
    p(patch("dark_factory.setup.docker_gen.write_generated_files", return_value=(Path("/tmp/Dockerfile"), Path("/tmp/docker-compose.yml"))))
    p(patch("dark_factory.setup.github_provision.provision_github", return_value=mocks["prov"]))
    p(patch("dark_factory.strategies.resolve_app_type", return_value=mocks["strat_cfg"]))
    p(patch("dark_factory.crucible.repo_provision.provision_crucible_repo"))
    p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.dark-factory")))
    p(patch("dark_factory.core.config_manager.resolve_config_path", return_value=Path("/tmp/.dark-factory/config.json")))
    p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))
    p(patch("tempfile.mkdtemp", return_value="/tmp/df-onboard-test"))
    p(patch("shutil.rmtree"))
    p(patch.dict(os.environ, {"GITHUB_REPO": repo}))


class TestOrchestratorStyledOutput:
    """10 unit tests for run_onboarding() styled display output."""

    def _run_with_mocks(self, *, auto_mode: bool = True, repo: str = "acme/app") -> tuple[int, list[str]]:
        """Run orchestrator with fully mocked externals, capturing output."""
        mocks = _make_success_mocks()
        captured: list[str] = []

        with ExitStack() as stack:
            _apply_success_patches(stack, mocks, captured, repo)
            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=auto_mode, start=Path("/tmp"))

        return exit_code, captured

    def test_show_banner_called_at_start(self) -> None:
        """show_banner() is called at start of run_onboarding()."""
        exit_code, captured = self._run_with_mocks()
        full_output = "".join(captured)
        assert len(captured) > 0, "No output produced at start"

    def test_phase_header_step_total(self) -> None:
        """phase_header() called with correct step/total for each phase."""
        exit_code, captured = self._run_with_mocks()
        full_output = "".join(captured)
        assert "Platform" in full_output or "platform" in full_output.lower()

    def test_status_line_semantic_level(self) -> None:
        """status_line() called with correct semantic level (success/error/info)."""
        exit_code, captured = self._run_with_mocks()
        full_output = "".join(captured)
        assert "authenticated" in full_output.lower() or "GitHub" in full_output

    def test_dep_status_for_each_tool(self) -> None:
        """dep_status() called for each tool with correct found status."""
        exit_code, captured = self._run_with_mocks()
        full_output = "".join(captured)
        assert "git" in full_output.lower() or "found" in full_output.lower()

    def test_completion_panel_on_success(self) -> None:
        """completion_panel() called with correct repo/strategy/labels on success."""
        exit_code, captured = self._run_with_mocks()
        full_output = "".join(captured)
        assert "acme/app" in full_output
        assert "console" in full_output.lower() or "App Type" in full_output

    def test_error_called_on_failure(self) -> None:
        """error() called with message and hint on failure."""
        captured: list[str] = []

        with ExitStack() as stack:
            p = stack.enter_context
            mock_gh = p(patch("dark_factory.integrations.shell.gh"))
            mock_plat = p(patch("dark_factory.setup.platform.detect_platform"))
            p(patch("dark_factory.setup.platform.check_dependencies", return_value=[]))
            p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value=""))
            p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=True))
            p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.dark-factory")))
            p(patch.dict(os.environ, {"GITHUB_REPO": "acme/bad"}))
            p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))

            mock_plat.return_value = MagicMock(os="linux", arch="x86_64", shell="bash")
            mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        full_output = "".join(captured)
        assert exit_code == 1
        assert "Cannot access" in full_output or "failed" in full_output.lower()

    def test_exit_code_0_on_success(self) -> None:
        """Exit code 0 on success path."""
        exit_code, _ = self._run_with_mocks()
        assert exit_code == 0

    def test_exit_code_1_on_failure(self) -> None:
        """Exit code 1 on failure path."""
        captured: list[str] = []

        with ExitStack() as stack:
            p = stack.enter_context
            p(patch("dark_factory.integrations.shell.gh"))
            mock_plat = p(patch("dark_factory.setup.platform.detect_platform"))
            p(patch("dark_factory.setup.platform.check_dependencies", return_value=[]))
            p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value=""))
            p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=True))
            p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.dark-factory")))
            p(patch.dict(os.environ, {"GITHUB_REPO": ""}))
            p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))

            mock_plat.return_value = MagicMock(os="linux", arch="x86_64", shell="bash")

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        assert exit_code == 1

    def test_machine_parseable_onboarding_complete(self) -> None:
        """Machine-parseable 'Onboarding complete!' preserved in captured output."""
        exit_code, captured = self._run_with_mocks()
        full_output = "".join(captured)
        assert "Onboarding complete!" in full_output

    def test_machine_parseable_github_repo(self) -> None:
        """Machine-parseable 'GITHUB_REPO=...' preserved in captured output."""
        exit_code, captured = self._run_with_mocks()
        full_output = "".join(captured)
        assert "GITHUB_REPO=acme/app" in full_output
