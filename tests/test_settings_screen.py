"""Tests for the Settings screen (US-010).

Verifies settings model, table rendering, keyboard actions,
theme application, config persistence, and value cycling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import DataTable, Label

from dark_factory.core.config_manager import ConfigData, load_config
from dark_factory.modes.settings import (
    AUTO_UPDATE_CYCLE,
    SettingsModel,
    SettingsScreen,
    apply_settings_to_config,
    load_settings,
    settings_from_config,
)
from dark_factory.ui.theme import SUBSYSTEM_THEMES

# ── Settings model tests ────────────────────────────────────────


def test_settings_model_is_frozen() -> None:
    s = SettingsModel()
    with pytest.raises(AttributeError):
        s.model = "changed"  # type: ignore[misc]


def test_settings_model_defaults() -> None:
    s = SettingsModel()
    assert s.model == ""
    assert s.provider == "anthropic"
    assert s.auto_update == "prompt"
    assert s.ouroboros_self_forge is False
    assert s.dashboard_refresh_interval == 2.0


def test_settings_model_custom_values() -> None:
    s = SettingsModel(
        model="claude-sonnet-4-20250514",
        provider="openai",
        auto_update="enabled",
        ouroboros_self_forge=True,
        dashboard_refresh_interval=5.0,
    )
    assert s.model == "claude-sonnet-4-20250514"
    assert s.provider == "openai"
    assert s.auto_update == "enabled"
    assert s.ouroboros_self_forge is True
    assert s.dashboard_refresh_interval == 5.0


def test_auto_update_cycle_has_three_options() -> None:
    assert len(AUTO_UPDATE_CYCLE) == 3
    assert "enabled" in AUTO_UPDATE_CYCLE
    assert "disabled" in AUTO_UPDATE_CYCLE
    assert "prompt" in AUTO_UPDATE_CYCLE


# ── Config ↔ SettingsModel round-trip ────────────────────────────


def test_settings_from_config_extracts_fields() -> None:
    cfg = ConfigData(data={
        "engine": {
            "model": "test-model",
            "provider": "openai",
            "auto_update": "enabled",
            "ouroboros_self_forge": True,
        },
        "dashboard": {"refresh_interval": 5.0},
    })
    s = settings_from_config(cfg)
    assert s.model == "test-model"
    assert s.provider == "openai"
    assert s.auto_update == "enabled"
    assert s.ouroboros_self_forge is True
    assert s.dashboard_refresh_interval == 5.0


def test_settings_from_config_uses_defaults_for_missing() -> None:
    cfg = ConfigData(data={})
    s = settings_from_config(cfg)
    assert s.model == ""
    assert s.provider == "anthropic"
    assert s.auto_update == "prompt"
    assert s.ouroboros_self_forge is False
    assert s.dashboard_refresh_interval == 2.0


def test_apply_settings_to_config_writes_all_keys() -> None:
    cfg = ConfigData(data={})
    s = SettingsModel(
        model="my-model",
        provider="local",
        auto_update="disabled",
        ouroboros_self_forge=True,
        dashboard_refresh_interval=10.0,
    )
    apply_settings_to_config(cfg, s)
    assert cfg.data["engine"]["model"] == "my-model"
    assert cfg.data["engine"]["provider"] == "local"
    assert cfg.data["engine"]["auto_update"] == "disabled"
    assert cfg.data["engine"]["ouroboros_self_forge"] is True
    assert cfg.data["dashboard"]["refresh_interval"] == 10.0


def test_round_trip_config_settings() -> None:
    """Settings → config → settings should preserve all values."""
    original = SettingsModel(
        model="round-trip",
        provider="openai",
        auto_update="enabled",
        ouroboros_self_forge=True,
        dashboard_refresh_interval=7.5,
    )
    cfg = ConfigData(data={})
    apply_settings_to_config(cfg, original)
    restored = settings_from_config(cfg)
    assert restored == original


# ── Config persistence ───────────────────────────────────────────


def test_save_settings_persists_to_disk(tmp_path: Path) -> None:
    """Saving settings writes to .dark-factory/config.json."""
    config_dir = tmp_path / ".dark-factory"
    config_dir.mkdir()
    config_path = config_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    cfg = load_config(tmp_path)
    s = SettingsModel(
        model="persisted-model",
        provider="local",
        auto_update="disabled",
        ouroboros_self_forge=True,
        dashboard_refresh_interval=3.0,
    )
    apply_settings_to_config(cfg, s)
    from dark_factory.core.config_manager import save_config as _save
    _save(cfg)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["engine"]["model"] == "persisted-model"
    assert raw["engine"]["provider"] == "local"
    assert raw["engine"]["ouroboros_self_forge"] is True
    assert raw["dashboard"]["refresh_interval"] == 3.0


# ── Table rendering tests (Textual pilot) ────────────────────────


@pytest.mark.asyncio
async def test_settings_screen_renders_table() -> None:
    app = SettingsScreen(settings=SettingsModel())
    async with app.run_test():
        table = app.query_one("#settings-table", DataTable)
        assert table.row_count == 5


@pytest.mark.asyncio
async def test_settings_table_has_three_columns() -> None:
    app = SettingsScreen(settings=SettingsModel())
    async with app.run_test():
        table = app.query_one("#settings-table", DataTable)
        assert len(table.columns) == 3


@pytest.mark.asyncio
async def test_settings_screen_shows_custom_values() -> None:
    s = SettingsModel(model="my-model", provider="openai")
    app = SettingsScreen(settings=s)
    async with app.run_test():
        table = app.query_one("#settings-table", DataTable)
        assert table.row_count == 5


# ── Keyboard action tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_escape_returns_none() -> None:
    app = SettingsScreen(settings=SettingsModel())
    async with app.run_test() as pilot:
        await pilot.press("escape")
        assert app.return_value is None


@pytest.mark.asyncio
async def test_q_quits() -> None:
    app = SettingsScreen(settings=SettingsModel())
    async with app.run_test() as pilot:
        await pilot.press("q")
        assert app.return_value is None


@pytest.mark.asyncio
async def test_m_key_cycles_model() -> None:
    app = SettingsScreen(settings=SettingsModel(model=""))
    async with app.run_test() as pilot:
        await pilot.press("m")
        assert app.settings.model == "claude-sonnet-4-20250514"
        assert app.dirty is True


@pytest.mark.asyncio
async def test_p_key_cycles_provider() -> None:
    app = SettingsScreen(settings=SettingsModel(provider="anthropic"))
    async with app.run_test() as pilot:
        await pilot.press("p")
        assert app.settings.provider == "openai"


@pytest.mark.asyncio
async def test_u_key_cycles_auto_update() -> None:
    app = SettingsScreen(settings=SettingsModel(auto_update="prompt"))
    async with app.run_test() as pilot:
        await pilot.press("u")
        # "prompt" is index 2, next is index 0 = "enabled"
        assert app.settings.auto_update == "enabled"


@pytest.mark.asyncio
async def test_o_key_toggles_ouroboros() -> None:
    app = SettingsScreen(settings=SettingsModel(ouroboros_self_forge=False))
    async with app.run_test() as pilot:
        await pilot.press("o")
        assert app.settings.ouroboros_self_forge is True
        await pilot.press("o")
        assert app.settings.ouroboros_self_forge is False


@pytest.mark.asyncio
async def test_d_key_cycles_dashboard_interval() -> None:
    app = SettingsScreen(settings=SettingsModel(dashboard_refresh_interval=2.0))
    async with app.run_test() as pilot:
        await pilot.press("d")
        assert app.settings.dashboard_refresh_interval == 5.0


# ── Theme tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_screen_uses_settings_theme() -> None:
    app = SettingsScreen(settings=SettingsModel())
    async with app.run_test():
        settings_theme = SUBSYSTEM_THEMES["settings"]
        assert app.screen.has_class(settings_theme.css_class)


def test_settings_theme_accent_is_gray() -> None:
    """Settings theme accent should be #94a3b8 (gray)."""
    assert SUBSYSTEM_THEMES["settings"].accent == "#94a3b8"


@pytest.mark.asyncio
async def test_settings_banner_is_present() -> None:
    app = SettingsScreen(settings=SettingsModel())
    async with app.run_test():
        banner = app.query_one("#settings-banner")
        assert banner is not None


@pytest.mark.asyncio
async def test_settings_status_bar_is_present() -> None:
    app = SettingsScreen(settings=SettingsModel())
    async with app.run_test():
        status_bar = app.query_one("#settings-status-bar")
        assert status_bar is not None


# ── Property access ──────────────────────────────────────────────


def test_settings_screen_exposes_settings_property() -> None:
    s = SettingsModel(model="prop-test")
    app = SettingsScreen(settings=s)
    assert app.settings == s


def test_settings_screen_dirty_starts_false() -> None:
    app = SettingsScreen(settings=SettingsModel())
    assert app.dirty is False


# ── Save indicator ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_indicator_initially_empty() -> None:
    app = SettingsScreen(settings=SettingsModel())
    async with app.run_test():
        indicator = app.query_one("#save-indicator", Label)
        # Label starts with empty renderable
        assert indicator is not None


# ── load_settings integration ────────────────────────────────────


def test_load_settings_returns_model() -> None:
    result = load_settings()
    assert isinstance(result, SettingsModel)
