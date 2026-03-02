"""Foundry workspace list screen — manage configured workspaces.

Displays a DataTable of all configured workspaces with:
- Repository name
- Deploy strategy (web / console)
- Status (active / paused)

Keyboard actions:
- [a] Add a new workspace
- [Enter] Drill into per-workspace configuration
- [Escape] Return to main menu
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Label, Static

from dark_factory.ui.theme import (
    COMPACT_ICONS,
    PILLARS,
    THEME,
    apply_subsystem_theme,
    build_theme_css,
)

# ── Workspace model ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Workspace:
    """A single configured workspace entry."""

    repo: str
    strategy: str  # "web" | "console"
    status: str    # "active" | "paused"


def load_workspaces(config_root: Path | None = None) -> list[Workspace]:
    """Load configured workspaces from ``.dark-factory/config.json``.

    Returns the current workspace list.  When no persistent config
    exists yet, returns an empty list.
    """
    from dark_factory.modes.foundry_onboard import load_workspace_configs  # noqa: PLC0415

    entries = load_workspace_configs(config_root=config_root)
    return [
        Workspace(repo=e.repo, strategy=e.strategy, status=e.status)
        for e in entries
    ]


# ── Widgets ──────────────────────────────────────────────────────


class FoundryBanner(Static):
    """Header banner for the Foundry screen."""

    def compose(self) -> ComposeResult:
        yield Label(
            f"[bold {THEME.primary_light}]"
            f"\n"
            f"     [{PILLARS.obelisk}]{COMPACT_ICONS['foundry']}[/{PILLARS.obelisk}]  "
            f"Foundry — Workspace Manager\n"
            f"[/]"
            f"[{THEME.text_muted}]"
            f"     Manage deploy targets and workspace configurations\n"
            f"[/{THEME.text_muted}]"
        )


class FoundryStatusBar(Static):
    """Hint bar below the table showing keyboard shortcut help."""

    def compose(self) -> ComposeResult:
        yield Label(
            f"[{THEME.text_muted}]"
            f"  [bold]a[/bold] add workspace  "
            f"\u2502  [bold]Enter[/bold] configure  "
            f"\u2502  [bold]Esc[/bold] back"
            f"[/{THEME.text_muted}]"
        )


# ── Status colour helpers ────────────────────────────────────────

_STATUS_COLOUR: dict[str, str] = {
    "active": THEME.success,
    "paused": THEME.warning,
}

_STRATEGY_COLOUR: dict[str, str] = {
    "web": THEME.info,
    "console": THEME.text_accent,
}


# ── Main application ─────────────────────────────────────────────

_FOUNDRY_CSS = f"""
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

#foundry-banner {{
    height: auto;
    padding: 1 2;
    background: {THEME.bg_panel};
    border: tall {THEME.primary_light};
    margin: 1 2;
}}

#workspace-table {{
    height: auto;
    min-height: 5;
    margin: 0 2;
    padding: 1 0;
    background: {THEME.bg_panel};
    border: tall {THEME.border};
}}

#foundry-status-bar {{
    height: auto;
    padding: 0 2;
    margin: 0 2;
}}

#empty-message {{
    height: auto;
    padding: 1 4;
    margin: 0 2;
    color: {THEME.text_muted};
}}
""" + build_theme_css()


class FoundryScreen(App[str | None]):
    """Foundry workspace list screen.

    Returns ``"add"`` when the user presses [a] to add a workspace,
    the repo name when Enter is pressed on a row, or ``None`` on
    back / quit.
    """

    TITLE = "Foundry — Workspaces"
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("a", "add_workspace", "Add Workspace"),
        Binding("q", "quit", "Quit"),
    ]
    CSS = _FOUNDRY_CSS

    def __init__(
        self,
        workspaces: list[Workspace] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._workspaces = workspaces if workspaces is not None else load_workspaces()

    @property
    def workspaces(self) -> list[Workspace]:
        """Return the current workspace list."""
        return self._workspaces

    # ── Compose ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            FoundryBanner(id="foundry-banner"),
            DataTable(id="workspace-table"),
            Label(
                "  No workspaces configured. Press [bold]a[/bold] to add one.",
                id="empty-message",
            ),
            FoundryStatusBar(id="foundry-status-bar"),
        )
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────

    def on_mount(self) -> None:
        """Apply Foundry theme and populate the workspace table."""
        apply_subsystem_theme(self, "foundry")
        self._setup_table()
        self._refresh_table()

    def _setup_table(self) -> None:
        """Add columns to the workspace DataTable."""
        table: DataTable[Any] = self.query_one("#workspace-table", DataTable)
        table.add_columns("Repo", "Strategy", "Status")
        table.cursor_type = "row"

    def _refresh_table(self) -> None:
        """Populate or clear workspace table rows."""
        table: DataTable[Any] = self.query_one("#workspace-table", DataTable)
        empty_msg = self.query_one("#empty-message", Label)
        table.clear()

        if not self._workspaces:
            table.display = False
            empty_msg.display = True
            return

        table.display = True
        empty_msg.display = False
        for ws in self._workspaces:
            s_colour = _STATUS_COLOUR.get(ws.status, THEME.text)
            t_colour = _STRATEGY_COLOUR.get(ws.strategy, THEME.text)
            table.add_row(
                ws.repo,
                f"[{t_colour}]{ws.strategy}[/{t_colour}]",
                f"[{s_colour}]{ws.status}[/{s_colour}]",
                key=ws.repo,
            )

    # ── Actions ───────────────────────────────────────────────

    def action_add_workspace(self) -> None:
        """Handle [a] — signal intent to add a workspace."""
        self.exit("add")

    def action_go_back(self) -> None:
        """Handle [Escape] — return to main menu."""
        self.exit(None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle [Enter] on a table row — drill into workspace config."""
        if event.row_key and event.row_key.value is not None:
            self.exit(str(event.row_key.value))


def run_foundry_tui(
    workspaces: list[Workspace] | None = None,
) -> str | None:
    """Launch the Foundry workspace list screen.

    Returns
    -------
    str | None
        ``"add"`` if the user wants to add a workspace, the repo name
        if they pressed Enter on a row, or ``None`` on back/quit.
    """
    app = FoundryScreen(workspaces=workspaces)
    return app.run()
