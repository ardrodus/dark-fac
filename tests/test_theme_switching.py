"""Tests for subsystem color theming (US-005).

Verifies theme definitions, CSS generation, and dynamic theme switching
via Textual CSS class swapping.
"""

from __future__ import annotations

import pytest

from dark_factory.modes.interactive import MENU_ITEMS, InteractiveApp
from dark_factory.ui.theme import (
    ALL_THEME_CLASSES,
    SUBSYSTEM_THEMES,
    SubsystemTheme,
    build_theme_css,
)

# ── Theme definitions ─────────────────────────────────────────────


def test_subsystem_themes_has_all_required_keys() -> None:
    required = {"default", "sentinel", "dark_forge", "crucible", "ouroboros", "foundry", "settings"}
    assert set(SUBSYSTEM_THEMES.keys()) == required


def test_all_theme_accent_colors() -> None:
    """Each subsystem has its correct accent color."""
    expected = {
        "default": "#e2e8f0",
        "sentinel": "#3b82f6",
        "dark_forge": "#f97316",
        "crucible": "#f59e0b",
        "ouroboros": "#7c3aed",
        "foundry": "#a78bfa",
        "settings": "#94a3b8",
    }
    for name, color in expected.items():
        assert SUBSYSTEM_THEMES[name].accent == color, f"Wrong accent for {name}"


def test_all_theme_classes_is_frozenset() -> None:
    assert isinstance(ALL_THEME_CLASSES, frozenset)
    assert len(ALL_THEME_CLASSES) == len(SUBSYSTEM_THEMES)


def test_subsystem_theme_is_frozen() -> None:
    theme = SUBSYSTEM_THEMES["sentinel"]
    with pytest.raises(AttributeError):
        theme.accent = "#000000"  # type: ignore[misc]


def test_each_theme_has_unique_css_class() -> None:
    classes = [t.css_class for t in SUBSYSTEM_THEMES.values()]
    assert len(classes) == len(set(classes))


def test_all_themes_are_subsystem_theme_instances() -> None:
    for theme in SUBSYSTEM_THEMES.values():
        assert isinstance(theme, SubsystemTheme)


# ── CSS generation ────────────────────────────────────────────────


def test_build_theme_css_contains_all_theme_classes() -> None:
    css = build_theme_css()
    for theme in SUBSYSTEM_THEMES.values():
        assert f"Screen.{theme.css_class}" in css


def test_build_theme_css_contains_header_rules() -> None:
    css = build_theme_css()
    assert "Header" in css


def test_build_theme_css_contains_themed_border_rules() -> None:
    css = build_theme_css()
    assert ".themed-border" in css


def test_build_theme_css_contains_accent_colors() -> None:
    css = build_theme_css()
    for theme in SUBSYSTEM_THEMES.values():
        assert theme.accent in css


def test_build_theme_css_contains_datatable_header_rules() -> None:
    css = build_theme_css()
    assert ".datatable--header" in css


def test_build_theme_css_contains_listview_highlight_rules() -> None:
    css = build_theme_css()
    assert ".listview--highlight" in css


# ── Menu items have subsystem field ───────────────────────────────


def test_menu_items_have_subsystem_field() -> None:
    for item in MENU_ITEMS:
        assert item.subsystem != ""
        assert item.subsystem in SUBSYSTEM_THEMES


# ── Dynamic theme switching (Textual pilot) ──────────────────────


@pytest.mark.asyncio
async def test_app_starts_with_default_theme() -> None:
    app = InteractiveApp()
    async with app.run_test():
        assert app.screen.has_class("theme-default")


@pytest.mark.asyncio
async def test_app_starts_without_subsystem_themes() -> None:
    app = InteractiveApp()
    async with app.run_test():
        non_default = ALL_THEME_CLASSES - {"theme-default"}
        for cls in non_default:
            assert not app.screen.has_class(cls)


@pytest.mark.asyncio
async def test_arrow_down_applies_crucible_theme() -> None:
    """Pressing Down moves highlight from Dark Forge to Crucible."""
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("down")
        assert app.screen.has_class("theme-crucible")
        assert not app.screen.has_class("theme-default")


@pytest.mark.asyncio
async def test_escape_resets_to_default_theme() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("down")  # Switch to Crucible theme
        await pilot.press("escape")  # Reset
        assert app.screen.has_class("theme-default")
        assert not app.screen.has_class("theme-crucible")


@pytest.mark.asyncio
async def test_theme_switches_between_subsystems() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("down")  # Crucible
        assert app.screen.has_class("theme-crucible")
        await pilot.press("down")  # Ouroboros
        assert app.screen.has_class("theme-ouroboros")
        assert not app.screen.has_class("theme-crucible")


@pytest.mark.asyncio
async def test_menu_banner_has_themed_border_class() -> None:
    app = InteractiveApp()
    async with app.run_test():
        banner = app.query_one("#menu-banner")
        assert banner.has_class("themed-border")


@pytest.mark.asyncio
async def test_only_one_theme_class_active_at_a_time() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("down")  # Crucible
        active = [cls for cls in ALL_THEME_CLASSES if app.screen.has_class(cls)]
        assert len(active) == 1
        assert active[0] == "theme-crucible"
