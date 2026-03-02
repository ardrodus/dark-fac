"""Tests for the per-workspace configuration screen (US-008).

Verifies data model, config display, keyboard actions, deploy pipeline
resolution, and theme application.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dark_factory.modes.foundry_config import (
    _DEPLOY_PIPELINE_NOT_CONFIGURED,
    WorkspaceConfig,
    WorkspaceConfigScreen,
    has_custom_deploy_dot,
    resolve_deploy_pipeline,
)
from dark_factory.ui.theme import SUBSYSTEM_THEMES

# ── Sample configs ───────────────────────────────────────────────

SAMPLE_CONFIG = WorkspaceConfig(
    repo="acme/web-app",
    strategy="web",
    scan_mode="full",
    watched_branch="main",
    webhook_status="enabled",
    last_forge_run="2h ago",
    last_crucible_verdict="GO",
    deploy_pipeline="custom/deploy.dot",
    status="active",
)

MINIMAL_CONFIG = WorkspaceConfig(repo="acme/minimal")

UNCONFIGURED_CONFIG = WorkspaceConfig(
    repo="acme/new-repo",
    strategy="console",
    scan_mode="off",
    watched_branch="develop",
    webhook_status="disabled",
    last_forge_run="never",
    last_crucible_verdict="none",
    deploy_pipeline=_DEPLOY_PIPELINE_NOT_CONFIGURED,
    status="paused",
)


# ── WorkspaceConfig model tests ──────────────────────────────────


def test_workspace_config_is_frozen() -> None:
    with pytest.raises(AttributeError):
        SAMPLE_CONFIG.strategy = "console"  # type: ignore[misc]


def test_workspace_config_fields() -> None:
    c = SAMPLE_CONFIG
    assert c.repo == "acme/web-app"
    assert c.strategy == "web"
    assert c.scan_mode == "full"
    assert c.watched_branch == "main"
    assert c.webhook_status == "enabled"
    assert c.last_forge_run == "2h ago"
    assert c.last_crucible_verdict == "GO"
    assert c.deploy_pipeline == "custom/deploy.dot"
    assert c.status == "active"


def test_workspace_config_defaults() -> None:
    c = MINIMAL_CONFIG
    assert c.repo == "acme/minimal"
    assert c.strategy == "console"
    assert c.scan_mode == "full"
    assert c.watched_branch == "main"
    assert c.webhook_status == "disabled"
    assert c.last_forge_run == "never"
    assert c.last_crucible_verdict == "none"
    assert c.deploy_pipeline == _DEPLOY_PIPELINE_NOT_CONFIGURED
    assert c.status == "active"


# ── Deploy pipeline resolution tests ─────────────────────────────


def test_has_custom_deploy_dot_returns_false_when_none() -> None:
    assert has_custom_deploy_dot(None) is False


def test_has_custom_deploy_dot_returns_false_when_no_file(tmp_path: Path) -> None:
    assert has_custom_deploy_dot(tmp_path) is False


def test_has_custom_deploy_dot_returns_true_when_exists(tmp_path: Path) -> None:
    dot_dir = tmp_path / ".dark-factory" / "pipelines"
    dot_dir.mkdir(parents=True)
    (dot_dir / "deploy.dot").write_text("digraph { build -> deploy }")
    assert has_custom_deploy_dot(tmp_path) is True


def test_resolve_deploy_pipeline_not_configured() -> None:
    result = resolve_deploy_pipeline(None)
    assert result == _DEPLOY_PIPELINE_NOT_CONFIGURED


def test_resolve_deploy_pipeline_no_file(tmp_path: Path) -> None:
    result = resolve_deploy_pipeline(tmp_path)
    assert result == _DEPLOY_PIPELINE_NOT_CONFIGURED


def test_resolve_deploy_pipeline_with_custom_file(tmp_path: Path) -> None:
    dot_dir = tmp_path / ".dark-factory" / "pipelines"
    dot_dir.mkdir(parents=True)
    dot_file = dot_dir / "deploy.dot"
    dot_file.write_text("digraph { build -> deploy }")
    result = resolve_deploy_pipeline(tmp_path)
    assert result == str(dot_file)


def test_deploy_pipeline_default_text() -> None:
    """AC: Deploy pipeline shows 'default (empty - not configured)' if no custom deploy.dot."""
    assert _DEPLOY_PIPELINE_NOT_CONFIGURED == "default (empty - not configured)"


# ── Screen rendering tests (Textual pilot) ───────────────────────


@pytest.mark.asyncio
async def test_config_screen_renders_banner() -> None:
    app = WorkspaceConfigScreen(config=SAMPLE_CONFIG)
    async with app.run_test():
        banner = app.query_one("#config-banner")
        assert banner is not None


@pytest.mark.asyncio
async def test_config_screen_renders_detail_panel() -> None:
    app = WorkspaceConfigScreen(config=SAMPLE_CONFIG)
    async with app.run_test():
        detail = app.query_one("#config-detail")
        assert detail is not None


@pytest.mark.asyncio
async def test_config_screen_renders_action_bar() -> None:
    app = WorkspaceConfigScreen(config=SAMPLE_CONFIG)
    async with app.run_test():
        action_bar = app.query_one("#config-action-bar")
        assert action_bar is not None


@pytest.mark.asyncio
async def test_config_screen_exposes_config_property() -> None:
    app = WorkspaceConfigScreen(config=SAMPLE_CONFIG)
    assert app.config is SAMPLE_CONFIG


@pytest.mark.asyncio
async def test_config_screen_shows_all_fields() -> None:
    """AC: Shows deploy strategy, scan mode, branch, webhook, forge, crucible, pipeline."""
    app = WorkspaceConfigScreen(config=SAMPLE_CONFIG)
    async with app.run_test():
        detail = app.query_one("#config-detail")
        assert detail is not None


# ── Theme tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_screen_uses_foundry_theme() -> None:
    app = WorkspaceConfigScreen(config=SAMPLE_CONFIG)
    async with app.run_test():
        foundry_theme = SUBSYSTEM_THEMES["foundry"]
        assert app.screen.has_class(foundry_theme.css_class)


# ── Keyboard action tests ────────────────────────────────────────


_KEY_BINDINGS = {
    "s": "change_strategy",
    "m": "change_scan_mode",
    "b": "change_branch",
    "w": "configure_webhooks",
    "p": "configure_pipeline",
    "d": "remove",
    "r": "rescan_baseline",
    "escape": None,
    "q": None,
}


@pytest.mark.asyncio
async def test_keyboard_actions() -> None:
    """All keyboard bindings return their expected action values."""
    for key, expected in _KEY_BINDINGS.items():
        app = WorkspaceConfigScreen(config=SAMPLE_CONFIG)
        async with app.run_test() as pilot:
            await pilot.press(key)
            assert app.return_value == expected, f"Key '{key}' expected {expected!r}, got {app.return_value!r}"


# ── Unconfigured workspace display ───────────────────────────────


@pytest.mark.asyncio
async def test_unconfigured_workspace_renders() -> None:
    """AC: Default deploy pipeline text shown for unconfigured workspace."""
    app = WorkspaceConfigScreen(config=UNCONFIGURED_CONFIG)
    async with app.run_test():
        detail = app.query_one("#config-detail")
        assert detail is not None
        assert app.config.deploy_pipeline == _DEPLOY_PIPELINE_NOT_CONFIGURED
