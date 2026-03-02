"""Tests for the interactive TUI main menu (factory.modes.interactive)."""

from __future__ import annotations

import pytest
from textual.widgets import ListView

from factory.modes.interactive import (
    MENU_ITEMS,
    InteractiveApp,
    MenuItem,
    MenuOption,
)

# ── Menu item data tests ─────────────────────────────────────────


def test_menu_has_five_items() -> None:
    assert len(MENU_ITEMS) == 5


def test_menu_keys_are_1_through_5() -> None:
    keys = [item.key for item in MENU_ITEMS]
    assert keys == ["1", "2", "3", "4", "5"]


def test_menu_titles() -> None:
    titles = [item.title for item in MENU_ITEMS]
    assert titles == ["Dark Forge", "Crucible", "Ouroboros", "Foundry", "Settings"]


def test_menu_descriptions() -> None:
    descriptions = [item.description for item in MENU_ITEMS]
    assert descriptions == [
        "Build an issue",
        "Validate a build",
        "Self-improve / update",
        "Manage workspaces",
        "Configure factory",
    ]


def test_menu_item_is_frozen() -> None:
    item = MENU_ITEMS[0]
    with pytest.raises(AttributeError):
        item.title = "changed"  # type: ignore[misc]


# ── App composition tests (Textual pilot) ────────────────────────


@pytest.mark.asyncio
async def test_app_renders_menu_list() -> None:
    app = InteractiveApp()
    async with app.run_test():
        list_view = app.query_one("#menu-list", ListView)
        assert len(list_view.children) == 5


@pytest.mark.asyncio
async def test_keyboard_1_selects_dark_forge() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("1")
        assert app.return_value == "1"


@pytest.mark.asyncio
async def test_keyboard_2_selects_crucible() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("2")
        assert app.return_value == "2"


@pytest.mark.asyncio
async def test_keyboard_3_selects_ouroboros() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("3")
        assert app.return_value == "3"


@pytest.mark.asyncio
async def test_keyboard_4_selects_foundry() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("4")
        assert app.return_value == "4"


@pytest.mark.asyncio
async def test_keyboard_5_selects_settings() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("5")
        assert app.return_value == "5"


@pytest.mark.asyncio
async def test_quit_returns_none() -> None:
    app = InteractiveApp()
    async with app.run_test() as pilot:
        await pilot.press("q")
        assert app.return_value is None


@pytest.mark.asyncio
async def test_menu_banner_is_present() -> None:
    app = InteractiveApp()
    async with app.run_test():
        banner = app.query_one("#menu-banner")
        assert banner is not None


@pytest.mark.asyncio
async def test_menu_option_item_property() -> None:
    item = MenuItem(key="x", title="Test", description="desc", color="#fff")
    option = MenuOption(item)
    assert option.item is item
