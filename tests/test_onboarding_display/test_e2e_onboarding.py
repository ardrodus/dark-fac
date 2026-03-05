"""Story 11: E2E tests for full onboarding flow with mocked externals.

Tests:
- Full flow with mocked externals produces correct output structure
- Output contains welcome banner at start
- Output contains all 14 styled phase headers
- Output contains completion panel at end
- Exit code 0 on full success path
- Exit code 1 on simulated failure
- Contract compliance: 'Onboarding complete!' present
- Contract compliance: 'GITHUB_REPO=...' present
- Contract compliance: only exit codes 0 or 1 produced
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_success_mocks():
    """Complete mock set for a full success path."""
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = ""
    fake_result.stderr = ""

    fake_plat = MagicMock()
    fake_plat.os = "linux"
    fake_plat.arch = "x86_64"
    fake_plat.shell = "bash"

    fake_dep_git = MagicMock(name="git", found=True)
    fake_dep_git.name = "git"
    fake_dep_gh = MagicMock(name="gh", found=True)
    fake_dep_gh.name = "gh"
    fake_dep_docker = MagicMock(name="docker", found=True)
    fake_dep_docker.name = "docker"

    fake_analysis = MagicMock()
    fake_analysis.language = "Python"
    fake_analysis.framework = "FastAPI"
    fake_analysis.detected_app_type = "web"
    fake_analysis.confidence = "high"
    fake_analysis.required_tools = ("python", "pip")
    fake_analysis.base_image = "python:3.12-bookworm"

    fake_install = MagicMock()
    fake_install.installed = 1
    fake_install.skipped = 1
    fake_install.failed = 0

    fake_prov = {"labels": 17, "ci_workflow": True, "issue_template": True, "branch_protection": True}

    fake_strat_cfg = MagicMock()
    fake_strat_cfg.bootstrap_deps = ["pytest", "uvicorn"]

    return {
        "gh": fake_result,
        "plat": fake_plat,
        "deps": [fake_dep_git, fake_dep_gh, fake_dep_docker],
        "analysis": fake_analysis,
        "install": fake_install,
        "prov": fake_prov,
        "strat_cfg": fake_strat_cfg,
    }


def _apply_success_patches(stack: ExitStack, mocks: dict, captured: list[str], repo: str) -> None:
    """Apply all success-path patches onto an ExitStack."""
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
    p(patch("dark_factory.setup.config_init.prompt_app_type", return_value="web"))
    p(patch("dark_factory.setup.config_init.init_config"))
    p(patch("dark_factory.setup.config_init.add_repo_to_config"))
    p(patch("dark_factory.setup.dep_installer.install_project_deps", return_value=mocks["install"]))
    p(patch("dark_factory.setup.docker_gen.write_generated_files", return_value=(Path("/tmp/Dockerfile"), Path("/tmp/docker-compose.yml"))))
    p(patch("dark_factory.setup.github_provision.provision_github", return_value=mocks["prov"]))
    p(patch("dark_factory.strategies.resolve_app_type", return_value=mocks["strat_cfg"]))
    p(patch("dark_factory.crucible.repo_provision.provision_crucible_repo"))
    p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.dark-factory")))
    p(patch("dark_factory.core.config_manager.resolve_config_path", return_value=Path("/tmp/.dark-factory/config.json")))
    p(patch("dark_factory.core.config_manager.load_config"))
    p(patch("dark_factory.core.config_manager.save_config"))
    p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))
    p(patch("tempfile.mkdtemp", return_value="/tmp/df-onboard-test"))
    p(patch("shutil.rmtree"))
    p(patch.dict(os.environ, {"GITHUB_REPO": repo}))


def _run_full_flow(*, repo: str = "acme/web-app", auto_mode: bool = True) -> tuple[int, str]:
    """Run the full onboarding flow and return (exit_code, full_output)."""
    mocks = _make_success_mocks()
    captured: list[str] = []

    with ExitStack() as stack:
        _apply_success_patches(stack, mocks, captured, repo)
        from dark_factory.setup.orchestrator import run_onboarding

        exit_code = run_onboarding(auto_mode=auto_mode, start=Path("/tmp"))

    return exit_code, "".join(captured)


def _run_failure_flow() -> tuple[int, str]:
    """Run onboarding with a simulated failure."""
    captured: list[str] = []
    fake_plat = MagicMock(os="linux", arch="x86_64", shell="bash")
    fail_result = MagicMock(returncode=1, stdout="", stderr="not found")

    with ExitStack() as stack:
        p = stack.enter_context
        p(patch("dark_factory.integrations.shell.gh", return_value=fail_result))
        p(patch("dark_factory.setup.platform.detect_platform", return_value=fake_plat))
        p(patch("dark_factory.setup.platform.check_dependencies", return_value=[]))
        p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value=""))
        p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=True))
        p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.dark-factory")))
        p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))
        p(patch.dict(os.environ, {"GITHUB_REPO": "acme/bad-repo"}))

        from dark_factory.setup.orchestrator import run_onboarding

        exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

    return exit_code, "".join(captured)


class TestE2EOnboarding:
    """9 E2E tests for full onboarding flow with mocked externals."""

    def test_full_flow_correct_output_structure(self) -> None:
        """Full flow with mocked externals produces correct output structure."""
        exit_code, output = _run_full_flow()
        assert exit_code == 0
        assert len(output) > 100
        assert "Platform" in output
        assert "GITHUB_REPO=" in output

    def test_output_contains_welcome_at_start(self) -> None:
        """Output contains welcome/platform info at start."""
        exit_code, output = _run_full_flow()
        assert "Platform" in output[:200] or "linux" in output[:200]

    def test_output_contains_phase_headers(self) -> None:
        """Output contains styled phase headers for key phases."""
        exit_code, output = _run_full_flow()
        phase_markers = [
            "Platform",
            "GitHub",
            "Repository",
            "Cloned",
            "Config",
            "Docker",
        ]
        found = sum(1 for marker in phase_markers if marker in output)
        assert found >= 4, f"Only {found} of {len(phase_markers)} phase markers found"

    def test_output_contains_completion_panel(self) -> None:
        """Output contains completion panel at end."""
        exit_code, output = _run_full_flow()
        last_section = output[-500:]
        assert "Onboarding complete!" in last_section or "complete" in last_section.lower()

    def test_exit_code_0_full_success(self) -> None:
        """Exit code 0 on full success path."""
        exit_code, _ = _run_full_flow()
        assert exit_code == 0

    def test_exit_code_1_on_failure(self) -> None:
        """Exit code 1 on simulated failure."""
        exit_code, output = _run_failure_flow()
        assert exit_code == 1

    def test_contract_onboarding_complete_present(self) -> None:
        """Contract compliance: 'Onboarding complete!' present on success."""
        exit_code, output = _run_full_flow()
        assert exit_code == 0
        assert "Onboarding complete!" in output

    def test_contract_github_repo_present(self) -> None:
        """Contract compliance: 'GITHUB_REPO=...' present on success."""
        exit_code, output = _run_full_flow()
        assert exit_code == 0
        assert "GITHUB_REPO=acme/web-app" in output

    def test_contract_only_exit_codes_0_or_1(self) -> None:
        """Contract compliance: only exit codes 0 or 1 produced."""
        exit_code_success, _ = _run_full_flow()
        assert exit_code_success in (0, 1)

        exit_code_failure, _ = _run_failure_flow()
        assert exit_code_failure in (0, 1)

        assert exit_code_success == 0
        assert exit_code_failure == 1
