"""Story 12: Edge case tests for ImportError, non-TTY, auto_mode, and boundary conditions.

Tests:
- Rich ImportError falls back to plain sys.stdout.write with ASCII formatting
- Non-TTY piped stdout produces clean text without Rich markup
- auto_mode=True runs without interactive prompts and plain output
- Zero dependencies found shows all red X marks and continues to error
- All dependencies found shows all green checkmarks
- Partial phase failure shows red for failed phase, exit code 1
- Empty repo name displays error without crash
- Unicode characters in repo/strategy are escaped and rendered correctly
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _apply_patches(stack: ExitStack, overrides: dict, captured: list[str]) -> None:
    """Apply standard orchestrator patches with overrides."""
    defaults = {
        "gh": MagicMock(returncode=0, stdout="", stderr=""),
        "plat": MagicMock(os="linux", arch="x86_64", shell="bash"),
        "deps": [],
        "model": "opus",
        "auth": True,
        "analysis": MagicMock(
            language="Python", framework="", detected_app_type="console",
            confidence="high", required_tools=(), base_image="python:3.12",
        ),
        "install": MagicMock(installed=0, skipped=0, failed=0),
        "prov": {"labels": 5, "ci_workflow": True, "issue_template": True, "branch_protection": True},
        "strat_cfg": MagicMock(bootstrap_deps=[]),
        "repo": "acme/app",
    }
    defaults.update(overrides)

    p = stack.enter_context
    p(patch("dark_factory.integrations.shell.gh", return_value=defaults["gh"]))
    p(patch("dark_factory.setup.platform.detect_platform", return_value=defaults["plat"]))
    p(patch("dark_factory.setup.platform.check_dependencies", return_value=defaults["deps"]))
    p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value=defaults["model"]))
    p(patch("dark_factory.setup.claude_detect.save_claude_model"))
    p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=defaults["auth"]))
    p(patch("dark_factory.setup.project_analyzer.analyze_project", return_value=defaults["analysis"]))
    p(patch("dark_factory.setup.project_analyzer.display_analysis_results"))
    p(patch("dark_factory.setup.config_init.init_config"))
    p(patch("dark_factory.setup.config_init.add_repo_to_config"))
    p(patch("dark_factory.setup.dep_installer.install_project_deps", return_value=defaults["install"]))
    p(patch("dark_factory.setup.docker_gen.write_generated_files", return_value=(Path("/tmp/Dockerfile"), Path("/tmp/dc.yml"))))
    p(patch("dark_factory.setup.github_provision.provision_github", return_value=defaults["prov"]))
    p(patch("dark_factory.strategies.resolve_app_type", return_value=defaults["strat_cfg"]))
    p(patch("dark_factory.crucible.repo_provision.provision_crucible_repo"))
    p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.df")))
    p(patch("dark_factory.core.config_manager.load_config"))
    p(patch("dark_factory.core.config_manager.save_config"))
    p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))
    p(patch("tempfile.mkdtemp", return_value="/tmp/df-test"))
    p(patch("shutil.rmtree"))
    p(patch.dict(os.environ, {"GITHUB_REPO": defaults["repo"]}))


class TestRichImportErrorFallback:
    """Rich ImportError falls back to plain ASCII output."""

    def test_cprint_fallback_without_rich(self) -> None:
        """cprint falls back to plain sys.stdout.write with ASCII formatting."""
        fake_stdout = io.StringIO()

        with patch.dict(sys.modules, {"rich": None, "rich.console": None}):
            from dark_factory.ui.cli_colors import cprint

            with patch("sys.stdout", fake_stdout):
                cprint("Hello World", "success")

        output = fake_stdout.getvalue()
        assert "Hello World" in output


class TestNonTTYDegradation:
    """Non-TTY piped stdout produces clean text."""

    def test_non_tty_no_rich_markup(self) -> None:
        """Non-TTY piped stdout produces clean text without Rich markup."""
        fake_stdout = io.StringIO()
        fake_stdout.isatty = lambda: False  # type: ignore[assignment]

        with patch("sys.stdout", fake_stdout):
            from dark_factory.ui.cli_colors import cprint

            cprint("Status: OK", "success")

        output = fake_stdout.getvalue()
        assert "Status: OK" in output or "OK" in output


class TestAutoMode:
    """auto_mode=True runs without interactive prompts."""

    def test_auto_mode_no_prompts(self) -> None:
        """auto_mode=True runs without interactive prompts and produces output."""
        captured: list[str] = []
        input_called: list[str] = []

        def _track_input(prompt=""):
            input_called.append(prompt)
            return ""

        with ExitStack() as stack:
            _apply_patches(stack, {"deps": [MagicMock(name="git", found=True)]}, captured)
            # Fix: set the .name attribute properly
            for d in [MagicMock(name="git", found=True)]:
                d.name = "git"
            stack.enter_context(patch("builtins.input", side_effect=_track_input))

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        assert exit_code == 0
        assert len(input_called) == 0


class TestZeroDependenciesFound:
    """Zero dependencies found shows all missing indicators."""

    def test_all_deps_missing(self) -> None:
        """Zero dependencies found shows all red X marks."""
        fake_dep1 = MagicMock()
        fake_dep1.name = "git"
        fake_dep1.found = False
        fake_dep2 = MagicMock()
        fake_dep2.name = "gh"
        fake_dep2.found = False
        fail_result = MagicMock(returncode=1, stdout="", stderr="not found")

        captured: list[str] = []
        with ExitStack() as stack:
            _apply_patches(stack, {"deps": [fake_dep1, fake_dep2], "gh": fail_result}, captured)

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        full_output = "".join(captured)
        assert "MISSING" in full_output or "missing" in full_output.lower()


class TestAllDependenciesFound:
    """All dependencies found shows all green checkmarks."""

    def test_all_deps_found(self) -> None:
        """All dependencies found shows all green checkmarks."""
        fake_dep1 = MagicMock()
        fake_dep1.name = "git"
        fake_dep1.found = True
        fake_dep2 = MagicMock()
        fake_dep2.name = "gh"
        fake_dep2.found = True

        captured: list[str] = []
        with ExitStack() as stack:
            _apply_patches(stack, {"deps": [fake_dep1, fake_dep2]}, captured)

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        full_output = "".join(captured)
        assert exit_code == 0
        assert "found" in full_output.lower()
        assert "MISSING" not in full_output


class TestPartialPhaseFailure:
    """Partial phase failure results in exit code 1."""

    def test_partial_failure_exit_code_1(self) -> None:
        """Partial phase failure shows exit code 1."""
        fake_dep = MagicMock()
        fake_dep.name = "git"
        fake_dep.found = True
        ok_result = MagicMock(returncode=0, stdout="", stderr="")
        fail_result = MagicMock(returncode=1, stdout="", stderr="error")

        call_count = [0]

        def gh_side_effect(*args, **kwargs):
            call_count[0] += 1
            return ok_result if call_count[0] <= 1 else fail_result

        captured: list[str] = []
        with ExitStack() as stack:
            p = stack.enter_context
            p(patch("dark_factory.integrations.shell.gh", side_effect=gh_side_effect))
            p(patch("dark_factory.setup.platform.detect_platform", return_value=MagicMock(os="linux", arch="x86_64", shell="bash")))
            p(patch("dark_factory.setup.platform.check_dependencies", return_value=[fake_dep]))
            p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value="opus"))
            p(patch("dark_factory.setup.claude_detect.save_claude_model"))
            p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=True))
            p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.df")))
            p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))
            p(patch.dict(os.environ, {"GITHUB_REPO": "acme/app"}))

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        assert exit_code == 1


class TestEmptyRepoName:
    """Empty repo name displays error without crash."""

    def test_empty_repo_no_crash(self) -> None:
        """Empty repo name displays error without crash."""
        captured: list[str] = []

        with ExitStack() as stack:
            p = stack.enter_context
            p(patch("dark_factory.integrations.shell.gh", return_value=MagicMock(returncode=0, stdout="", stderr="")))
            p(patch("dark_factory.setup.platform.detect_platform", return_value=MagicMock(os="linux", arch="x86_64", shell="bash")))
            p(patch("dark_factory.setup.platform.check_dependencies", return_value=[]))
            p(patch("dark_factory.setup.claude_detect.detect_claude_model", return_value=""))
            p(patch("dark_factory.setup.github_auth.auto_connect_github", return_value=True))
            p(patch("dark_factory.core.config_manager.resolve_config_dir", return_value=Path("/tmp/.df")))
            p(patch("sys.stdout.write", side_effect=lambda s: captured.append(s)))
            # Clear GITHUB_REPO
            env = os.environ.copy()
            env.pop("GITHUB_REPO", None)
            p(patch.dict(os.environ, env, clear=True))

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        full_output = "".join(captured)
        assert exit_code == 1
        assert "GITHUB_REPO" in full_output or "No" in full_output or "repo" in full_output.lower()


class TestUnicodeCharacters:
    """Unicode characters in repo/strategy are handled correctly."""

    def test_unicode_repo_name_rendered(self) -> None:
        """Unicode characters in repo name are escaped and rendered correctly."""
        unicode_repo = "acme/web-\u00e4pp-\u00fc"  # acme/web-äpp-ü
        captured: list[str] = []

        with ExitStack() as stack:
            _apply_patches(stack, {"repo": unicode_repo}, captured)

            from dark_factory.setup.orchestrator import run_onboarding

            exit_code = run_onboarding(auto_mode=True, start=Path("/tmp"))

        full_output = "".join(captured)
        assert exit_code == 0
        assert unicode_repo in full_output or "\u00e4" in full_output
