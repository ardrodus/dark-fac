"""Story 7: Unit tests for workspace bootstrap and github_provision.py styled display.

Tests:
- workspace_bootstrap: success returns BootstrapResult, runtime missing, deps fail partial
- github_provision: provisioning steps use stage icons, success green, failure with hint
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ── workspace_bootstrap tests ────────────────────────────────────


class TestWorkspaceBootstrapDisplay:
    """3 tests for workspace bootstrap via _bootstrap_workspace_env."""

    @staticmethod
    def _mock_pipeline_result(data: dict) -> MagicMock:
        """Build a fake pipeline result with bootstrap JSON output."""
        import json

        result = MagicMock()
        result.context = {"codergen.bootstrap.output": json.dumps(data)}
        return result

    def test_success_returns_bootstrap_result(self) -> None:
        """Successful bootstrap returns BootstrapResult with all fields True."""
        pipe_res = self._mock_pipeline_result({
            "runtime_ok": True, "env_created": True, "deps_installed": True,
            "env_path": ".venv", "errors": [],
        })

        from dark_factory.setup.orchestrator import _bootstrap_workspace_env

        result = _bootstrap_workspace_env(
            "/tmp/workspace", MagicMock(language="Python", framework="", test_cmd="pytest"),
            _run_pipeline=lambda name, ctx: pipe_res,
        )

        assert result.success
        assert result.runtime_ok
        assert result.env_path == ".venv"

    def test_runtime_missing_returns_failure(self) -> None:
        """Missing runtime returns BootstrapResult with runtime_ok=False."""
        pipe_res = self._mock_pipeline_result({
            "runtime_ok": False, "env_created": False, "deps_installed": False,
            "env_path": "", "errors": ["python3 not found"],
        })

        from dark_factory.setup.orchestrator import _bootstrap_workspace_env

        result = _bootstrap_workspace_env(
            "/tmp/workspace", MagicMock(language="Python", framework="", test_cmd="pytest"),
            _run_pipeline=lambda name, ctx: pipe_res,
        )

        assert not result.success
        assert not result.runtime_ok
        assert "python3 not found" in result.errors

    def test_deps_fail_returns_partial(self) -> None:
        """Runtime OK but deps fail returns partial result."""
        pipe_res = self._mock_pipeline_result({
            "runtime_ok": True, "env_created": True, "deps_installed": False,
            "env_path": ".venv", "errors": ["pip install failed"],
        })

        from dark_factory.setup.orchestrator import _bootstrap_workspace_env

        result = _bootstrap_workspace_env(
            "/tmp/workspace", MagicMock(language="Python", framework="FastAPI", test_cmd="pytest"),
            _run_pipeline=lambda name, ctx: pipe_res,
        )

        assert not result.success
        assert result.runtime_ok
        assert result.env_created
        assert not result.deps_installed


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
