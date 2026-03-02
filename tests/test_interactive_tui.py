"""Tests for the interactive TUI main menu (factory.modes.interactive)."""

from __future__ import annotations

import pytest
from textual.widgets import ListView

from dark_factory.modes.interactive import (
    MENU_ITEMS,
    InteractiveApp,
    MenuItem,
    MenuOption,
)

# ── Menu item data tests ─────────────────────────────────────────


def test_menu_items_structure() -> None:
    """MENU_ITEMS has 5 frozen items with correct keys, titles, and descriptions."""
    assert len(MENU_ITEMS) == 5
    assert [item.key for item in MENU_ITEMS] == ["1", "2", "3", "4", "5"]
    assert [item.title for item in MENU_ITEMS] == [
        "Dark Forge", "Crucible", "Ouroboros", "Foundry", "Settings",
    ]
    assert [item.description for item in MENU_ITEMS] == [
        "Build an issue",
        "Validate a build",
        "Self-improve / update",
        "Manage workspaces",
        "Configure factory",
    ]
    with pytest.raises(AttributeError):
        MENU_ITEMS[0].title = "changed"  # type: ignore[misc]


# ── App composition tests (Textual pilot) ────────────────────────


@pytest.mark.asyncio
async def test_app_renders_menu_list() -> None:
    app = InteractiveApp()
    async with app.run_test():
        list_view = app.query_one("#menu-list", ListView)
        assert len(list_view.children) == 5


@pytest.mark.asyncio
async def test_keyboard_1_through_5_selects_menu_items() -> None:
    """Keys 1-5 each return their key string as the selection."""
    for key in ("1", "2", "3", "4", "5"):
        app = InteractiveApp()
        async with app.run_test() as pilot:
            await pilot.press(key)
            assert app.return_value == key, f"Key '{key}' expected return_value '{key}'"


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
