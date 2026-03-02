"""Interactive mode — Textual TUI main menu for Dark Factory.

Presents a main menu with five options:

1. **Dark Forge** — build an issue
2. **Crucible** — validate a build
3. **Ouroboros** — self-improve / update
4. **Foundry** — manage workspaces
5. **Settings** — configure factory

Each option is navigable via keyboard ([1]–[5]) and mouse click.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from factory import __version__
from factory.ui.theme import (
    COMPACT_ICONS,
    PILLARS,
    THEME,
    apply_subsystem_theme,
    build_theme_css,
    reset_theme,
)

# ── Menu item model ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MenuItem:
    """A single main-menu entry."""

    key: str
    title: str
    description: str
    color: str
    subsystem: str = ""


MENU_ITEMS: tuple[MenuItem, ...] = (
    MenuItem(
        key="1", title="Dark Forge", description="Build an issue",
        color=PILLARS.dark_forge, subsystem="dark_forge",
    ),
    MenuItem(
        key="2", title="Crucible", description="Validate a build",
        color=PILLARS.crucible, subsystem="crucible",
    ),
    MenuItem(
        key="3", title="Ouroboros", description="Self-improve / update",
        color=PILLARS.ouroboros, subsystem="ouroboros",
    ),
    MenuItem(
        key="4", title="Foundry", description="Manage workspaces",
        color=PILLARS.obelisk, subsystem="foundry",
    ),
    MenuItem(
        key="5", title="Settings", description="Configure factory",
        color=THEME.text_muted, subsystem="settings",
    ),
)


# ── Widgets ──────────────────────────────────────────────────────


class MenuOption(ListItem):
    """A single selectable menu row inside the ListView."""

    def __init__(self, item: MenuItem) -> None:
        super().__init__()
        self._item = item

    @property
    def item(self) -> MenuItem:
        """Return the underlying menu item."""
        return self._item

    def compose(self) -> ComposeResult:
        item = self._item
        yield Label(
            f"  [{item.color}][{item.key}][/{item.color}]  "
            f"[bold]{item.title}[/bold]  "
            f"[{THEME.text_muted}]{item.description}[/{THEME.text_muted}]"
        )


class MenuBanner(Static):
    """ASCII-art banner at the top of the menu screen."""

    def compose(self) -> ComposeResult:
        yield Label(
            f"[bold {THEME.primary}]"
            f"\n"
            f"     Dark Factory  v{__version__}\n"
            f"[/]"
            f"[{THEME.text_muted}]"
            f"     Automated Issue-Dispatch Pipeline\n"
            f"\n"
            f"     [{PILLARS.sentinel}]{COMPACT_ICONS['sentinel']}[/{PILLARS.sentinel}] Sentinel  "
            f"[{PILLARS.dark_forge}]{COMPACT_ICONS['dark_forge']}[/{PILLARS.dark_forge}] Forge  "
            f"[{PILLARS.crucible}]{COMPACT_ICONS['crucible']}[/{PILLARS.crucible}] Crucible\n"
            f"     [{PILLARS.obelisk}]{COMPACT_ICONS['obelisk']}[/{PILLARS.obelisk}] Obelisk   "
            f"[{PILLARS.ouroboros}]{COMPACT_ICONS['ouroboros']}[/{PILLARS.ouroboros}] Ouroboros\n"
            f"[/{THEME.text_muted}]"
        )


class StatusBar(Static):
    """Hint bar below the menu showing keyboard shortcut help."""

    def compose(self) -> ComposeResult:
        yield Label(
            f"[{THEME.text_muted}]"
            f"  Press [bold]1[/bold]-[bold]5[/bold] or click to select  "
            f"\u2502  [bold]q[/bold] quit"
            f"[/{THEME.text_muted}]"
        )


# ── Main application ─────────────────────────────────────────────


_MENU_CSS = f"""
Screen {{
    background: {THEME.bg_dark};
}}

Header {{
    background: {THEME.bg_header};
    color: {THEME.text};
}}

Footer {{
    background: {THEME.bg_panel};
    color: {THEME.text_muted};
}}

#menu-banner {{
    height: auto;
    padding: 1 2;
    background: {THEME.bg_panel};
    border: tall {THEME.primary};
    margin: 1 2;
}}

#menu-list {{
    height: auto;
    min-height: 7;
    margin: 0 2;
    padding: 1 0;
    background: {THEME.bg_dark};
}}

#menu-list > ListItem {{
    height: 3;
    padding: 0 1;
    background: {THEME.bg_panel};
    margin: 0 0 1 0;
}}

#menu-list > ListItem:hover {{
    background: {THEME.border};
}}

#menu-list:focus > .listview--highlight-top {{
    background: {THEME.border};
}}

#menu-list:focus > .listview--highlight {{
    background: {THEME.border};
}}

#status-bar {{
    height: auto;
    padding: 0 2;
    margin: 0 2;
}}
""" + build_theme_css()


class InteractiveApp(App[str | None]):
    """Dark Factory interactive TUI main menu.

    Returns the selected menu item's key string (e.g. ``"1"``)
    when the user activates an option, or ``None`` on quit.
    """

    TITLE = "Dark Factory"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "reset_theme", "Back", show=False),
        Binding("1", "select_1", "Dark Forge", show=False),
        Binding("2", "select_2", "Crucible", show=False),
        Binding("3", "select_3", "Ouroboros", show=False),
        Binding("4", "select_4", "Foundry", show=False),
        Binding("5", "select_5", "Settings", show=False),
    ]
    CSS = _MENU_CSS

    # ── Compose ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            MenuBanner(id="menu-banner", classes="themed-border"),
            ListView(
                *(MenuOption(item) for item in MENU_ITEMS),
                id="menu-list",
            ),
            StatusBar(id="status-bar"),
        )
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────

    def on_mount(self) -> None:
        """Apply neutral default theme on startup."""
        self._skip_initial_highlight = True
        reset_theme(self)

    # ── Theme switching ───────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Preview subsystem theme when menu item highlight changes."""
        if self._skip_initial_highlight:
            self._skip_initial_highlight = False
            return
        if isinstance(event.item, MenuOption) and event.item.item.subsystem:
            apply_subsystem_theme(self, event.item.item.subsystem)
        else:
            reset_theme(self)

    def action_reset_theme(self) -> None:
        """Reset to default neutral theme (bound to Escape)."""
        reset_theme(self)

    # ── Keyboard actions ([1]–[5]) ────────────────────────────

    def _select_by_key(self, key: str) -> None:
        """Activate the menu item matching *key*."""
        for item in MENU_ITEMS:
            if item.key == key:
                self.exit(key)
                return

    def action_select_1(self) -> None:
        self._select_by_key("1")

    def action_select_2(self) -> None:
        self._select_by_key("2")

    def action_select_3(self) -> None:
        self._select_by_key("3")

    def action_select_4(self) -> None:
        self._select_by_key("4")

    def action_select_5(self) -> None:
        self._select_by_key("5")

    # ── Mouse / Enter activation via ListView ─────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle mouse click or Enter on a list item."""
        if isinstance(event.item, MenuOption):
            self.exit(event.item.item.key)


def run_interactive_tui() -> str | None:
    """Launch the interactive TUI and return the selected menu key.

    Returns
    -------
    str | None
        The key of the selected option (``"1"`` – ``"5"``), or
        ``None`` if the user quit without selecting.
    """
    app = InteractiveApp()
    return app.run()
