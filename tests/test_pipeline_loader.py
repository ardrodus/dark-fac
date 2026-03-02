"""Tests for factory.pipeline.loader -- pipeline discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from dark_factory.pipeline.loader import discover_pipelines


@pytest.fixture()
def builtins_dir(tmp_path: Path) -> Path:
    """Create a fake built-in pipelines directory with sample DOT files."""
    d = tmp_path / "builtins"
    d.mkdir()
    (d / "dark_forge.dot").write_text("digraph { }", encoding="utf-8")
    (d / "sentinel.dot").write_text("digraph { }", encoding="utf-8")
    (d / "deploy.dot").write_text("digraph { }", encoding="utf-8")
    return d


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """Create a project root with .dark-factory directory."""
    root = tmp_path / "project"
    root.mkdir()
    (root / ".dark-factory").mkdir()
    return root


# ── Basic discovery ─────────────────────────────────────────────


class TestBuiltinDiscovery:
    """Built-in pipelines from factory/pipelines/*.dot."""

    def test_discovers_builtin_pipelines(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert "dark_forge" in result
        assert "sentinel" in result
        assert "deploy" in result

    def test_returns_dict_str_path(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert isinstance(result, dict)
        for name, path in result.items():
            assert isinstance(name, str)
            assert isinstance(path, Path)

    def test_names_are_stems(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert all("." not in name for name in result)

    def test_empty_builtins_dir(self, tmp_path: Path, project_root: Path) -> None:
        empty = tmp_path / "empty_builtins"
        empty.mkdir()
        result = discover_pipelines(
            project_root=project_root, builtins_dir=empty
        )
        assert result == {}

    def test_missing_builtins_dir(self, tmp_path: Path, project_root: Path) -> None:
        missing = tmp_path / "nonexistent"
        result = discover_pipelines(
            project_root=project_root, builtins_dir=missing
        )
        assert result == {}

    def test_ignores_non_dot_files(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        (builtins_dir / "readme.txt").write_text("not a pipeline", encoding="utf-8")
        (builtins_dir / "config.json").write_text("{}", encoding="utf-8")
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert "readme" not in result
        assert "config" not in result


# ── User custom pipelines ───────────────────────────────────────


class TestUserPipelines:
    """User custom pipelines from .dark-factory/pipelines/*.dot."""

    def test_discovers_user_pipelines(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        user_dir = project_root / ".dark-factory" / "pipelines"
        user_dir.mkdir(parents=True)
        (user_dir / "custom.dot").write_text("digraph { }", encoding="utf-8")
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert "custom" in result

    def test_user_overrides_builtin(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        user_dir = project_root / ".dark-factory" / "pipelines"
        user_dir.mkdir(parents=True)
        (user_dir / "deploy.dot").write_text(
            "digraph { custom }", encoding="utf-8"
        )
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert "deploy" in result
        # Should point to the user copy, not the built-in
        assert result["deploy"].parent == user_dir

    def test_no_user_dir_is_fine(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        # .dark-factory exists but no pipelines/ subdir
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert len(result) == 3  # just builtins

    def test_user_adds_without_clobbering(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        user_dir = project_root / ".dark-factory" / "pipelines"
        user_dir.mkdir(parents=True)
        (user_dir / "my_pipeline.dot").write_text("digraph { }", encoding="utf-8")
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        # All builtins still present, plus the user one
        assert "dark_forge" in result
        assert "sentinel" in result
        assert "deploy" in result
        assert "my_pipeline" in result


# ── Config overrides ────────────────────────────────────────────


class TestConfigOverrides:
    """Pipeline overrides from pipeline.overrides in config.json."""

    def test_config_override_takes_precedence(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        # Create a config.json with an override
        config_dir = project_root / ".dark-factory"
        config_dir.mkdir(exist_ok=True)
        override_file = project_root / "special" / "my_deploy.dot"
        override_file.parent.mkdir(parents=True)
        override_file.write_text("digraph { special }", encoding="utf-8")
        (config_dir / "config.json").write_text(
            '{"pipeline": {"overrides": {"deploy": "special/my_deploy.dot"}}}',
            encoding="utf-8",
        )
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert result["deploy"] == override_file.resolve() or result["deploy"] == (
            project_root / "special" / "my_deploy.dot"
        )

    def test_config_override_missing_file_skipped(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        config_dir = project_root / ".dark-factory"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "config.json").write_text(
            '{"pipeline": {"overrides": {"ghost": "does/not/exist.dot"}}}',
            encoding="utf-8",
        )
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert "ghost" not in result

    def test_config_override_absolute_path(
        self, builtins_dir: Path, project_root: Path, tmp_path: Path
    ) -> None:
        abs_dot = tmp_path / "abs_pipeline.dot"
        abs_dot.write_text("digraph { }", encoding="utf-8")
        config_dir = project_root / ".dark-factory"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "config.json").write_text(
            f'{{"pipeline": {{"overrides": {{"custom_abs": "{abs_dot.as_posix()}"}}}}}}',
            encoding="utf-8",
        )
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert "custom_abs" in result
        assert result["custom_abs"] == abs_dot

    def test_no_config_file_is_fine(
        self, builtins_dir: Path, project_root: Path
    ) -> None:
        # No config.json at all -- should still return builtins
        result = discover_pipelines(
            project_root=project_root, builtins_dir=builtins_dir
        )
        assert len(result) == 3


# ── Integration with real builtins ──────────────────────────────


class TestRealBuiltins:
    """Verify discovery works against the actual factory/pipelines/ directory."""

    def test_discovers_shipped_pipelines(self, project_root: Path) -> None:
        result = discover_pipelines(project_root=project_root)
        # These are the DOT files created in US-106..US-112
        for name in ("sentinel", "dark_forge", "deploy", "crucible", "ouroboros"):
            assert name in result, f"Missing built-in pipeline: {name}"

    def test_all_values_are_existing_files(self, project_root: Path) -> None:
        result = discover_pipelines(project_root=project_root)
        for name, path in result.items():
            assert path.is_file(), f"Pipeline {name!r} points to missing file: {path}"
