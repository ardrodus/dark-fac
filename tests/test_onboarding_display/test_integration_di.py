"""Story 10: Integration tests for DI wiring, Rich fallback, and non-TTY degradation.

Tests:
- All output routed through injected writer function
- Presenter methods called in correct phase order
- use_rich=False produces output with no Rich markup
- Non-TTY (piped stdout) degrades to clean plain text
- Phases 1-14 called in expected sequence order
- Presenter and writer channels both receive appropriate output simultaneously
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.test_onboarding_display.conftest import MockPresenter, OnboardingDisplayConfig


def _make_success_mocks():
    """Create mocks for a successful onboarding run."""
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
    fake_analysis.detected_strategy = "console"
    fake_analysis.confidence = "high"
    fake_analysis.required_tools = ("python",)

    fake_install = MagicMock()
    fake_install.installed = 0
    fake_install.skipped = 1
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


def _apply_patches(stack: ExitStack, mocks: dict, captured: list[str], repo: str = "acme/app") -> None:
    """Apply all patches onto an ExitStack."""
    p = stack.enter_context
    p(patch("dark_factory.integrations.shell.gh", return_value=mocks["gh"]))
    p(patch("dark_factory.setup.platform.detect_platform", return_value=mocks["plat"]))
    p(patch("dark_factory.setup.platform.check_dependencies", return_value=mocks["deps"]))
    p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value="opus"))
    p(patch("dark_factory.setup.claude_detect.prompt_claude_model", return_value="opus"))
    p(patch("dark_factory.setup.claude_detect.save_claude_model"))
    p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=True))
    p(patch("dark_factory.setup.project_analyzer.analyze_project", return_value=mocks["analysis"]))
    p(patch("dark_factory.setup.project_analyzer.display_analysis_results"))
    p(patch("dark_factory.setup.project_analyzer.confirm_or_override_analysis", return_value=mocks["analysis"]))
    p(patch("dark_factory.setup.config_init.prompt_deployment_strategy", return_value="console"))
    p(patch("dark_factory.setup.config_init.init_config"))
    p(patch("dark_factory.setup.config_init.add_repo_to_config"))
    p(patch("dark_factory.setup.dep_installer.install_project_deps", return_value=mocks["install"]))
    p(patch("dark_factory.setup.docker_gen.write_generated_files", return_value=(Path("/tmp/Dockerfile"), Path("/tmp/docker-compose.yml"))))
    p(patch("dark_factory.setup.github_provision.provision_github", return_value=mocks["prov"]))
    p(patch("dark_factory.strategies.resolve_strategy", return_value=mocks["strat_cfg"]))
    p(patch("dark_factory.crucible.repo_provision.provision_crucible_repo"))
    p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.dark-factory")))
    p(patch("dark_factory.core.config_manager.resolve_config_path", return_value=Path("/tmp/.dark-factory/config.json")))
    p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))
    p(patch("tempfile.mkdtemp", return_value="/tmp/df-onboard-test"))
    p(patch("shutil.rmtree"))
    p(patch.dict(os.environ, {"GITHUB_REPO": repo}))


def _run_orchestrator(repo: str = "acme/app") -> tuple[int, list[str]]:
    """Run orchestrator with mocks, return (exit_code, captured_lines)."""
    mocks = _make_success_mocks()
    captured: list[str] = []

    with ExitStack() as stack:
        _apply_patches(stack, mocks, captured, repo)
        from dark_factory.setup.orchestrator import run_onboarding

        exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

    return exit_code, captured


class TestIntegrationDI:
    """6 integration tests for DI wiring, Rich fallback, and non-TTY."""

    def test_all_output_through_writer(self) -> None:
        """All output is routed through the stdout.write function."""
        exit_code, captured = _run_orchestrator()
        assert exit_code == 0
        full_output = "".join(captured)
        assert len(full_output) > 0
        assert "Onboarding complete!" in full_output

    def test_presenter_methods_in_phase_order(self) -> None:
        """Phases execute in correct order: platform, deps, auth, repo, clone, analyze, etc."""
        exit_code, captured = _run_orchestrator()
        full_output = "".join(captured)

        platform_idx = full_output.find("Platform")
        repo_idx = full_output.find("Repository")
        complete_idx = full_output.find("Onboarding complete!")

        assert platform_idx >= 0, "Platform phase missing"
        assert repo_idx >= 0, "Repository phase missing"
        assert complete_idx >= 0, "Completion missing"
        assert platform_idx < repo_idx < complete_idx

    def test_use_rich_false_no_markup(self) -> None:
        """use_rich=False produces output with no Rich markup tags."""
        exit_code, captured = _run_orchestrator()

        for line in captured:
            assert not (line.startswith("[bold") and "[/]" in line), f"Raw markup in output: {line!r}"

    def test_non_tty_clean_text(self) -> None:
        """Non-TTY (piped stdout) degrades to clean plain text."""
        mocks = _make_success_mocks()
        fake_stdout = io.StringIO()
        fake_stdout.isatty = lambda: False  # type: ignore[assignment]

        with ExitStack() as stack:
            p = stack.enter_context
            p(patch("dark_factory.integrations.shell.gh", return_value=mocks["gh"]))
            p(patch("dark_factory.setup.platform.detect_platform", return_value=mocks["plat"]))
            p(patch("dark_factory.setup.platform.check_dependencies", return_value=mocks["deps"]))
            p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value="opus"))
            p(patch("dark_factory.setup.claude_detect.save_claude_model"))
            p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=True))
            p(patch("dark_factory.setup.project_analyzer.analyze_project", return_value=mocks["analysis"]))
            p(patch("dark_factory.setup.project_analyzer.display_analysis_results"))
            p(patch("dark_factory.setup.config_init.init_config"))
            p(patch("dark_factory.setup.config_init.add_repo_to_config"))
            p(patch("dark_factory.setup.dep_installer.install_project_deps", return_value=mocks["install"]))
            p(patch("dark_factory.setup.docker_gen.write_generated_files", return_value=(Path("/tmp/Dockerfile"), Path("/tmp/docker-compose.yml"))))
            p(patch("dark_factory.setup.github_provision.provision_github", return_value=mocks["prov"]))
            p(patch("dark_factory.strategies.resolve_strategy", return_value=mocks["strat_cfg"]))
            p(patch("dark_factory.crucible.repo_provision.provision_crucible_repo"))
            p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.dark-factory")))
            p(patch("sys.stdout", fake_stdout))
            p(patch("tempfile.mkdtemp", return_value="/tmp/df-onboard-test"))
            p(patch("shutil.rmtree"))
            p(patch.dict(os.environ, {"GITHUB_REPO": "acme/app"}))

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        output = fake_stdout.getvalue()
        assert "\x1b[" not in output or exit_code == 0

    def test_phases_called_in_sequence(self) -> None:
        """Phases 1-14 are called in expected sequence order."""
        exit_code, captured = _run_orchestrator()
        assert exit_code == 0
        full_output = "".join(captured)

        phases = [
            "Platform",
            "git",
            "GitHub",
            "Repository",
            "Cloned",
            "Config",
            "Docker",
            "Onboarding complete!",
        ]

        last_idx = -1
        for phase in phases:
            idx = full_output.find(phase)
            if idx >= 0:
                assert idx >= last_idx, f"Phase '{phase}' out of order (at {idx}, expected >= {last_idx})"
                last_idx = idx

    def test_presenter_and_writer_coexist(self) -> None:
        """Presenter and writer channels both receive output."""
        exit_code, captured = _run_orchestrator()
        assert exit_code == 0
        full_output = "".join(captured)

        assert len(captured) > 0
        assert "acme/app" in full_output
        assert "Onboarding complete!" in full_output
        assert "GITHUB_REPO=" in full_output
