"""Foundry workspace screens — list + per-workspace config editor.

Two Textual TUI screens:

1. **FoundryScreen** — workspace list with DataTable.
   Returns ``"add"`` / repo name / ``None``.

2. **WorkspaceConfigScreen** — per-workspace config editor.
   Shows analysis info (read-only) and editable fields (app type, status).
   Returns ``"saved"`` when changes are persisted, ``None`` on back.

Keyboard actions (list):
- [a] Add a new workspace
- [Enter] Drill into per-workspace configuration
- [Escape] Return to main menu

Keyboard actions (config editor):
- [t] Toggle app type (web ↔ console)
- [s] Toggle status (active ↔ paused)
- [Enter] Save changes
- [Escape] Discard and go back
"""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)

# ── Workspace model ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Workspace:
    """A single configured workspace entry."""

    repo: str
    app_type: str  # "web" | "console"
    status: str    # "active" | "paused"


def load_workspaces(config_root: Path | None = None) -> list[Workspace]:
    """Load configured workspaces from ``.dark-factory/config.json``.

    Returns the current workspace list.  When no persistent config
    exists yet, returns an empty list.
    """
    from dark_factory.modes.foundry_onboard import load_workspace_configs  # noqa: PLC0415

    entries = load_workspace_configs(config_root=config_root)
    return [
        Workspace(repo=e.repo, app_type=e.app_type, status=e.status)
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

_APP_TYPE_COLOUR: dict[str, str] = {
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
        table.add_columns("Repo", "App Type", "Status")
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
            t_colour = _APP_TYPE_COLOUR.get(ws.app_type, THEME.text)
            table.add_row(
                ws.repo,
                f"[{t_colour}]{ws.app_type}[/{t_colour}]",
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


# ── Workspace config editor ─────────────────────────────────────


def _load_repo_config(repo: str) -> dict[str, Any]:
    """Load the repo entry + workspace_config from config.json."""
    from dark_factory.core.config_manager import resolve_config_path  # noqa: PLC0415

    path = resolve_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    for r in data.get("repos", []):
        if isinstance(r, dict) and r.get("name") == repo:
            return dict(r)
    return {}


def _save_repo_fields(repo: str, *, app_type: str, active: bool) -> None:
    """Persist app_type and active status for a repo entry."""
    from dark_factory.core.config_manager import load_config, save_config  # noqa: PLC0415

    cfg = load_config()
    for r in cfg.data.get("repos", []):
        if isinstance(r, dict) and r.get("name") == repo:
            r["active"] = active
            ws_cfg = r.get("workspace_config", {})
            if not isinstance(ws_cfg, dict):
                ws_cfg = {}
            ws_cfg["app_type"] = app_type
            r["workspace_config"] = ws_cfg
            break
    save_config(cfg)


_CONFIG_CSS = f"""
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

#config-banner {{
    height: auto;
    padding: 1 2;
    background: {THEME.bg_panel};
    border: tall {THEME.primary_light};
    margin: 1 2;
}}

#config-fields {{
    height: auto;
    margin: 0 2;
    padding: 1 2;
    background: {THEME.bg_panel};
    border: tall {THEME.border};
}}

#config-analysis {{
    height: auto;
    margin: 1 2;
    padding: 1 2;
    background: {THEME.bg_panel};
    border: tall {THEME.border};
}}

#config-status-bar {{
    height: auto;
    padding: 0 2;
    margin: 0 2;
}}
""" + build_theme_css()


class WorkspaceConfigScreen(App[str | None]):
    """Per-workspace config editor.

    Displays analysis info (read-only) and lets the user toggle
    app type and active/paused status.  Returns ``"saved"`` on
    Enter (persists changes) or ``None`` on Escape (discard).
    """

    TITLE = "Workspace Configuration"
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("t", "toggle_app_type", "Toggle App Type"),
        Binding("s", "toggle_status", "Toggle Status"),
        Binding("enter", "save", "Save"),
        Binding("q", "go_back", "Quit"),
    ]
    CSS = _CONFIG_CSS

    def __init__(self, repo: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._repo = repo
        self._entry = _load_repo_config(repo)
        ws_cfg = self._entry.get("workspace_config", {})
        if not isinstance(ws_cfg, dict):
            ws_cfg = {}
        self._app_type = str(ws_cfg.get("app_type", "console"))
        self._active = bool(self._entry.get("active", True))
        self._analysis: dict[str, Any] = ws_cfg.get("analysis", {})
        if not isinstance(self._analysis, dict):
            self._analysis = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(id="config-banner"),
            Static(id="config-fields"),
            Static(id="config-analysis"),
            Static(id="config-status-bar"),
        )
        yield Footer()

    def on_mount(self) -> None:
        apply_subsystem_theme(self, "foundry")
        self._refresh()

    def _refresh(self) -> None:
        """Rebuild all display panels from current state."""
        # Banner
        banner = self.query_one("#config-banner", Static)
        banner.update(
            f"[bold {THEME.primary_light}]"
            f"\n"
            f"     [{PILLARS.obelisk}]{COMPACT_ICONS['foundry']}[/{PILLARS.obelisk}]  "
            f"Workspace Config\n"
            f"[/]"
            f"[{THEME.text_muted}]"
            f"     {self._repo}\n"
            f"[/{THEME.text_muted}]"
        )

        # Editable fields
        t_colour = _APP_TYPE_COLOUR.get(self._app_type, THEME.text)
        s_colour = THEME.success if self._active else THEME.warning
        s_label = "active" if self._active else "paused"
        fields = self.query_one("#config-fields", Static)
        fields.update(
            f"  [bold]Editable[/bold]\n"
            f"\n"
            f"  [{THEME.text_muted}][t][/{THEME.text_muted}]  "
            f"App type:  [{t_colour}][bold]{self._app_type}[/bold][/{t_colour}]\n"
            f"  [{THEME.text_muted}][s][/{THEME.text_muted}]  "
            f"Status:    [{s_colour}][bold]{s_label}[/bold][/{s_colour}]\n"
        )

        # Analysis (read-only)
        a = self._analysis
        if a:
            lang = a.get("language", "")
            fw = a.get("framework", "")
            lang_display = f"{lang} / {fw}" if fw else (lang or "unknown")
            lines = [
                f"  [bold]Project Analysis[/bold]  [{THEME.text_muted}](read-only)[/{THEME.text_muted}]",
                "",
                f"  Language:    [{THEME.success}]{lang_display}[/{THEME.success}]",
            ]
            if a.get("description"):
                lines.append(f"  Description: [{THEME.text_muted}]{a['description']}[/{THEME.text_muted}]")
            if a.get("base_image"):
                lines.append(f"  Base image:  [{THEME.text_muted}]{a['base_image']}[/{THEME.text_muted}]")
            for lbl, key in (("Build", "build_cmd"), ("Test", "test_cmd"), ("Run", "run_cmd")):
                val = a.get(key, "")
                if val:
                    lines.append(f"  {lbl + ':':<11}  [{THEME.text_muted}]{val}[/{THEME.text_muted}]")
            for lbl, key in (("Tools", "required_tools"), ("Source", "source_dirs"), ("Tests", "test_dirs")):
                val = a.get(key, [])
                if val:
                    lines.append(f"  {lbl + ':':<11}  [{THEME.text_muted}]{', '.join(val)}[/{THEME.text_muted}]")
            analysis_panel = self.query_one("#config-analysis", Static)
            analysis_panel.update("\n".join(lines) + "\n")
        else:
            self.query_one("#config-analysis", Static).update(
                f"  [{THEME.text_muted}]No analysis data available[/{THEME.text_muted}]"
            )

        # Status bar
        self.query_one("#config-status-bar", Static).update(
            f"[{THEME.text_muted}]"
            f"  [bold]t[/bold] toggle app type  "
            f"\u2502  [bold]s[/bold] toggle status  "
            f"\u2502  [bold]Enter[/bold] save  "
            f"\u2502  [bold]Esc[/bold] discard"
            f"[/{THEME.text_muted}]"
        )

    def action_toggle_app_type(self) -> None:
        self._app_type = "console" if self._app_type == "web" else "web"
        self._refresh()

    def action_toggle_status(self) -> None:
        self._active = not self._active
        self._refresh()

    def action_save(self) -> None:
        _save_repo_fields(self._repo, app_type=self._app_type, active=self._active)
        self.exit("saved")

    def action_go_back(self) -> None:
        self.exit(None)


def run_workspace_config_tui(repo: str) -> str | None:
    """Launch the per-workspace config editor.

    Returns ``"saved"`` if changes were persisted, ``None`` on discard/back.
    """
    app = WorkspaceConfigScreen(repo)
    return app.run()
