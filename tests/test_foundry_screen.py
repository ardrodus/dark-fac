"""Tests for the Foundry workspace list screen (US-007).

Verifies workspace model, table rendering, keyboard actions,
theme application, and empty-state display.
"""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, Label

from factory.modes.foundry import (
    FoundryScreen,
    Workspace,
    load_workspaces,
)
from factory.ui.theme import SUBSYSTEM_THEMES

# ── Sample data ──────────────────────────────────────────────────

SAMPLE_WORKSPACES: list[Workspace] = [
    Workspace(repo="acme/web-app", strategy="web", status="active"),
    Workspace(repo="acme/cli-tool", strategy="console", status="active"),
    Workspace(repo="acme/data-pipeline", strategy="console", status="paused"),
]


# ── Workspace model tests ────────────────────────────────────────


def test_workspace_is_frozen() -> None:
    ws = Workspace(repo="test/repo", strategy="web", status="active")
    with pytest.raises(AttributeError):
        ws.repo = "changed"  # type: ignore[misc]


def test_workspace_fields() -> None:
    ws = Workspace(repo="org/repo", strategy="console", status="paused")
    assert ws.repo == "org/repo"
    assert ws.strategy == "console"
    assert ws.status == "paused"


def test_load_workspaces_returns_list() -> None:
    result = load_workspaces()
    assert isinstance(result, list)


# ── Table rendering tests (Textual pilot) ────────────────────────


@pytest.mark.asyncio
async def test_foundry_screen_renders_workspace_table() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test():
        table = app.query_one("#workspace-table", DataTable)
        assert table.row_count == 3


@pytest.mark.asyncio
async def test_each_row_shows_repo_strategy_status() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test():
        table = app.query_one("#workspace-table", DataTable)
        assert table.row_count == 3
        # Column headers: Repo, Strategy, Status
        assert len(table.columns) == 3


@pytest.mark.asyncio
async def test_empty_workspace_list_shows_message() -> None:
    app = FoundryScreen(workspaces=[])
    async with app.run_test():
        table = app.query_one("#workspace-table", DataTable)
        msg = app.query_one("#empty-message", Label)
        assert table.display is False
        assert msg.display is True


@pytest.mark.asyncio
async def test_nonempty_workspace_list_hides_empty_message() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test():
        table = app.query_one("#workspace-table", DataTable)
        msg = app.query_one("#empty-message", Label)
        assert table.display is True
        assert msg.display is False


# ── Keyboard action tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_key_returns_add() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test() as pilot:
        await pilot.press("a")
        assert app.return_value == "add"


@pytest.mark.asyncio
async def test_escape_returns_none() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test() as pilot:
        await pilot.press("escape")
        assert app.return_value is None


@pytest.mark.asyncio
async def test_q_quits() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test() as pilot:
        await pilot.press("q")
        assert app.return_value is None


@pytest.mark.asyncio
async def test_enter_on_row_returns_repo_name() -> None:
    """Pressing Enter on a highlighted row returns its repo name."""
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test() as pilot:
        # Focus the table and press Enter on the first row
        table = app.query_one("#workspace-table", DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("enter")
        assert app.return_value == "acme/web-app"


# ── Theme tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_foundry_screen_uses_foundry_theme() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test():
        foundry_theme = SUBSYSTEM_THEMES["foundry"]
        assert app.screen.has_class(foundry_theme.css_class)


@pytest.mark.asyncio
async def test_foundry_theme_accent_is_lighter_purple() -> None:
    """Foundry theme accent should be #a78bfa (lighter purple)."""
    assert SUBSYSTEM_THEMES["foundry"].accent == "#a78bfa"


@pytest.mark.asyncio
async def test_foundry_banner_is_present() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test():
        banner = app.query_one("#foundry-banner")
        assert banner is not None


@pytest.mark.asyncio
async def test_foundry_status_bar_is_present() -> None:
    app = FoundryScreen(workspaces=SAMPLE_WORKSPACES)
    async with app.run_test():
        status_bar = app.query_one("#foundry-status-bar")
        assert status_bar is not None


# ── Workspace property access ────────────────────────────────────


def test_foundry_screen_exposes_workspaces_property() -> None:
    ws = [Workspace(repo="x/y", strategy="web", status="active")]
    app = FoundryScreen(workspaces=ws)
    assert app.workspaces == ws


def test_foundry_screen_default_workspaces_empty() -> None:
    app = FoundryScreen()
    assert app.workspaces == []
