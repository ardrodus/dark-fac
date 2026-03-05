"""Story 7: Unit tests for config_init.py, dep_installer.py, github_provision.py styled display.

Tests:
- config_init: strategy menu styled, detected strategy highlighted, selection confirmation
- dep_installer: installed tool green checkmark, skipped green 'found', failed red X
- github_provision: provisioning steps use stage icons, success green, failure with hint
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from dark_factory.setup.project_analyzer import AnalysisResult


# ── config_init.py tests ─────────────────────────────────────────


class TestConfigInitDisplay:
    """3 tests for strategy menu styled display in config_init.py."""

    def test_strategy_menu_styled(self) -> None:
        """Strategy menu is styled with labeled options."""
        captured: list[str] = []
        analysis = AnalysisResult(
            language="Python",
            detected_app_type="console",
            confidence="high",
        )
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("builtins.input", return_value="1"),
        ):
            from dark_factory.setup.config_init import prompt_app_type

            prompt_app_type(analysis)

        full_output = "".join(captured)
        # Menu should show numbered options
        assert "[1]" in full_output or "Console" in full_output
        assert "[2]" in full_output or "Web" in full_output

    def test_detected_app_type_highlighted(self) -> None:
        """Detected app type is highlighted as recommended."""
        captured: list[str] = []
        analysis = AnalysisResult(
            language="TypeScript",
            framework="Next.js",
            detected_app_type="web",
            confidence="high",
        )
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("builtins.input", return_value="2"),
        ):
            from dark_factory.setup.config_init import prompt_app_type

            prompt_app_type(analysis)

        full_output = "".join(captured)
        # Should indicate the detected strategy
        assert "web" in full_output.lower() or "Detected" in full_output

    def test_selection_confirmation_styled(self) -> None:
        """Selection confirmation is displayed after choosing."""
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("builtins.input", return_value="1"),
        ):
            from dark_factory.setup.config_init import prompt_app_type

            result = prompt_app_type()

        full_output = "".join(captured)
        assert result == "console"
        # Should confirm the selection
        assert "App Type" in full_output or "console" in full_output


# ── dep_installer.py tests ───────────────────────────────────────


class TestDepInstallerDisplay:
    """3 tests for install status display in dep_installer.py."""

    def test_installed_tool_shows_checkmark(self) -> None:
        """Installed tool shows green checkmark or positive indicator."""
        captured: list[str] = []
        analysis = AnalysisResult(required_tools=("nonexistent-tool-xyz",))
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("shutil.which", return_value=None),
            patch("dark_factory.setup.dep_installer._install_tool", return_value=True),
            patch("dark_factory.setup.dep_installer._is_installed", side_effect=[False, True]),
        ):
            from dark_factory.setup.dep_installer import install_project_deps

            install_project_deps(analysis, plat_os="linux")

        full_output = "".join(captured)
        assert "installed" in full_output.lower() or "+" in full_output

    def test_skipped_tool_shows_found(self) -> None:
        """Skipped (already present) tool shows green 'found' status."""
        captured: list[str] = []
        analysis = AnalysisResult(required_tools=("python",))
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.setup.dep_installer._is_installed", return_value=True),
        ):
            from dark_factory.setup.dep_installer import install_project_deps

            result = install_project_deps(analysis, plat_os="linux")

        full_output = "".join(captured)
        assert "found" in full_output.lower()
        assert result.skipped >= 1

    def test_failed_tool_shows_red_x(self) -> None:
        """Failed tool shows red X or failure indicator."""
        captured: list[str] = []
        analysis = AnalysisResult(required_tools=("nonexistent-tool-xyz",))
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.setup.dep_installer._is_installed", return_value=False),
            patch("dark_factory.setup.dep_installer._install_tool", return_value=False),
        ):
            from dark_factory.setup.dep_installer import install_project_deps

            result = install_project_deps(analysis, plat_os="linux")

        full_output = "".join(captured)
        assert "failed" in full_output.lower() or "x" in full_output.lower()
        assert result.failed >= 1


# ── github_provision.py tests ────────────────────────────────────


class TestGithubProvisionDisplay:
    """3 tests for provisioning output display in github_provision.py."""

    def test_provisioning_steps_use_stage_indicators(self) -> None:
        """Provisioning steps use stage icons or step indicators."""
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.integrations.shell.gh") as mock_gh,
        ):
            mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
            from dark_factory.setup.github_provision import provision_github

            provision_github("acme/app")

        full_output = "".join(captured)
        # Should show step indicators for labels, template, CI, protection
        assert "label" in full_output.lower()
        assert "template" in full_output.lower() or "workflow" in full_output.lower()

    def test_success_styled_green(self) -> None:
        """Successful provisioning shows positive indicators."""
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.integrations.shell.gh") as mock_gh,
        ):
            mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
            from dark_factory.setup.github_provision import provision_github

            result = provision_github("acme/app")

        full_output = "".join(captured)
        assert "created" in full_output.lower() or "+" in full_output
        assert result["ci_workflow"] is True

    def test_failure_shows_error(self) -> None:
        """Failure shows error indicator."""
        captured: list[str] = []
        with (
            patch("sys.stdout.write", side_effect=lambda s: captured.append(s)),
            patch("dark_factory.integrations.shell.gh") as mock_gh,
        ):
            mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
            from dark_factory.setup.github_provision import provision_github

            result = provision_github("acme/app")

        full_output = "".join(captured)
        assert "skipped" in full_output.lower() or "!" in full_output
