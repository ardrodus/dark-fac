"""Tests for factory.engine.config -- US-204 config wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dark_factory.engine.config import (
    _DEFAULT_STYLESHEET,
    EngineConfig,
    _load_stylesheet,
    load_engine_config,
)


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal .dark-factory/ directory with config.json."""
    df = tmp_path / ".dark-factory"
    df.mkdir()
    config = {
        "engine": {
            "model": "claude-opus-4-6",
            "claude_path": "/usr/local/bin/claude",
            "deploy_strategy": "aws",
            "pipeline_timeout": 300,
        },
        "sentinel": {
            "scan_mode": "strict",
        },
    }
    (df / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def minimal_project_dir(tmp_path: Path) -> Path:
    """Create a .dark-factory/ with empty config.json (defaults only)."""
    df = tmp_path / ".dark-factory"
    df.mkdir()
    (df / "config.json").write_text("{}", encoding="utf-8")
    return tmp_path


class TestEngineConfig:
    """EngineConfig dataclass tests."""

    def test_defaults(self) -> None:
        cfg = EngineConfig()
        assert cfg.model == ""
        assert cfg.claude_path == "claude"
        assert cfg.deploy_strategy == "console"
        assert cfg.sentinel_scan_mode == "standard"
        assert cfg.pipeline_timeout == 600
        assert cfg.model_stylesheet == ""

    def test_frozen(self) -> None:
        cfg = EngineConfig()
        with pytest.raises(AttributeError):
            cfg.model = "nope"  # type: ignore[misc]


class TestLoadEngineConfig:
    """load_engine_config integration tests."""

    def test_reads_all_fields(self, project_dir: Path) -> None:
        cfg = load_engine_config(start=project_dir)
        assert cfg.model == "claude-opus-4-6"
        assert cfg.claude_path == "/usr/local/bin/claude"
        assert cfg.deploy_strategy == "aws"
        assert cfg.sentinel_scan_mode == "strict"
        assert cfg.pipeline_timeout == 300

    def test_defaults_when_empty_config(self, minimal_project_dir: Path) -> None:
        cfg = load_engine_config(start=minimal_project_dir)
        assert cfg.model == ""
        assert cfg.claude_path == "claude"
        assert cfg.deploy_strategy == "console"
        assert cfg.sentinel_scan_mode == "standard"
        assert cfg.pipeline_timeout == 600

    def test_invalid_timeout_uses_default(self, tmp_path: Path) -> None:
        df = tmp_path / ".dark-factory"
        df.mkdir()
        config = {"engine": {"pipeline_timeout": -10}}
        (df / "config.json").write_text(json.dumps(config), encoding="utf-8")

        cfg = load_engine_config(start=tmp_path)
        assert cfg.pipeline_timeout == 600

    def test_string_timeout_uses_default(self, tmp_path: Path) -> None:
        df = tmp_path / ".dark-factory"
        df.mkdir()
        config = {"engine": {"pipeline_timeout": "not-a-number"}}
        (df / "config.json").write_text(json.dumps(config), encoding="utf-8")

        cfg = load_engine_config(start=tmp_path)
        assert cfg.pipeline_timeout == 600


class TestLoadStylesheet:
    """Stylesheet loading tests."""

    def test_loads_custom_stylesheet(self, tmp_path: Path) -> None:
        df = tmp_path / ".dark-factory"
        df.mkdir()
        css = "* { llm_model: gpt-5; }"
        (df / "model-stylesheet.css").write_text(css, encoding="utf-8")

        result = _load_stylesheet(df)
        assert result == css

    def test_returns_default_when_missing(self, tmp_path: Path) -> None:
        df = tmp_path / ".dark-factory"
        df.mkdir()

        result = _load_stylesheet(df)
        assert result == _DEFAULT_STYLESHEET

    def test_config_includes_stylesheet(self, project_dir: Path) -> None:
        css = ".critical { llm_model: claude-opus-4-6; }"
        (project_dir / ".dark-factory" / "model-stylesheet.css").write_text(
            css, encoding="utf-8"
        )

        cfg = load_engine_config(start=project_dir)
        assert cfg.model_stylesheet == css

    def test_default_stylesheet_when_no_file(self, project_dir: Path) -> None:
        cfg = load_engine_config(start=project_dir)
        assert cfg.model_stylesheet == _DEFAULT_STYLESHEET
