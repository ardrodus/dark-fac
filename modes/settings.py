"""Settings screen — global factory configuration.

Displays and allows editing of factory-wide settings:

- **Model** — LLM model name (e.g. ``claude-sonnet-4-20250514``)
- **Provider** — LLM provider (e.g. ``anthropic``, ``openai``)
- **Auto-update policy** — ``enabled`` / ``disabled`` / ``prompt``
- **Ouroboros self-forge** — on / off toggle
- **Dashboard preferences** — refresh interval in seconds

Settings are persisted to ``.dark-factory/config.json``.

Keyboard actions:
- [m] Change model
- [p] Change provider
- [u] Cycle auto-update policy
- [o] Toggle Ouroboros self-forge
- [d] Change dashboard refresh interval
- [s] Save settings to disk
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

from dark_factory.core.config_manager import (
    ConfigData,
    get_config_value,
    load_config,
    save_config,
    set_config_value,
)
from dark_factory.ui.theme import (
    THEME,
    apply_subsystem_theme,
    build_theme_css,
)

# ── Settings model ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SettingsModel:
    """Snapshot of factory-wide settings displayed on the screen."""

    model: str = ""
    provider: str = "anthropic"
    auto_update: str = "prompt"  # "enabled" | "disabled" | "prompt"
    ouroboros_self_forge: bool = False
    dashboard_refresh_interval: float = 2.0


AUTO_UPDATE_CYCLE: tuple[str, ...] = ("enabled", "disabled", "prompt")


def load_settings(config_root: Path | None = None) -> SettingsModel:
    """Load settings from ``.dark-factory/config.json``.

    Maps config keys to :class:`SettingsModel` fields.  Missing keys
    fall back to the dataclass defaults.
    """
    cfg = load_config(config_root)
    return settings_from_config(cfg)


def settings_from_config(cfg: ConfigData) -> SettingsModel:
    """Extract a :class:`SettingsModel` from loaded config data."""
    return SettingsModel(
        model=get_config_value(cfg, "engine.model") or "",
        provider=get_config_value(cfg, "engine.provider") or "anthropic",
        auto_update=get_config_value(cfg, "engine.auto_update") or "prompt",
        ouroboros_self_forge=bool(
            get_config_value(cfg, "engine.ouroboros_self_forge"),
        ),
        dashboard_refresh_interval=float(
            get_config_value(cfg, "dashboard.refresh_interval") or 2.0,
        ),
    )


def apply_settings_to_config(cfg: ConfigData, settings: SettingsModel) -> None:
    """Write :class:`SettingsModel` fields back into the config data."""
    set_config_value(cfg, "engine.model", settings.model)
    set_config_value(cfg, "engine.provider", settings.provider)
    set_config_value(cfg, "engine.auto_update", settings.auto_update)
    set_config_value(cfg, "engine.ouroboros_self_forge", settings.ouroboros_self_forge)
    set_config_value(cfg, "dashboard.refresh_interval", settings.dashboard_refresh_interval)


# ── Widgets ──────────────────────────────────────────────────────


class SettingsBanner(Static):
    """Header banner for the Settings screen."""

    def compose(self) -> ComposeResult:
        yield Label(
            f"[bold {THEME.text_muted}]"
            f"\n"
            f"     \u2699  Settings — Configure Factory\n"
            f"[/]"
            f"[{THEME.text_muted}]"
            f"     Global factory-wide configuration options\n"
            f"[/{THEME.text_muted}]"
        )


class SettingsStatusBar(Static):
    """Hint bar showing keyboard shortcut help."""

    def compose(self) -> ComposeResult:
        yield Label(
            f"[{THEME.text_muted}]"
            f"  [bold]m[/bold] model  "
            f"\u2502  [bold]p[/bold] provider  "
            f"\u2502  [bold]u[/bold] auto-update  "
            f"\u2502  [bold]o[/bold] ouroboros  "
            f"\u2502  [bold]d[/bold] dashboard  "
            f"\u2502  [bold]s[/bold] save  "
            f"\u2502  [bold]Esc[/bold] back"
            f"[/{THEME.text_muted}]"
        )


# ── CSS ──────────────────────────────────────────────────────────

_SETTINGS_ACCENT = "#94a3b8"

_SETTINGS_CSS = f"""
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

#settings-banner {{
    height: auto;
    padding: 1 2;
    background: {THEME.bg_panel};
    border: tall {_SETTINGS_ACCENT};
    margin: 1 2;
}}

#settings-table {{
    height: auto;
    min-height: 7;
    margin: 0 2;
    padding: 1 0;
    background: {THEME.bg_panel};
    border: tall {THEME.border};
}}

#settings-status-bar {{
    height: auto;
    padding: 0 2;
    margin: 0 2;
}}

#save-indicator {{
    height: auto;
    padding: 0 4;
    margin: 0 2;
    color: {THEME.success};
}}
""" + build_theme_css()


# ── Value formatting helpers ─────────────────────────────────────


def _format_bool(value: bool) -> str:
    """Format a boolean as a coloured ON / OFF label."""
    if value:
        return f"[{THEME.success}]ON[/{THEME.success}]"
    return f"[{THEME.text_muted}]OFF[/{THEME.text_muted}]"


def _format_auto_update(policy: str) -> str:
    """Format the auto-update policy with colour."""
    colours: dict[str, str] = {
        "enabled": THEME.success,
        "disabled": THEME.error,
        "prompt": THEME.warning,
    }
    colour = colours.get(policy, THEME.text)
    return f"[{colour}]{policy}[/{colour}]"


# ── Main application ─────────────────────────────────────────────


class SettingsScreen(App[str | None]):
    """Settings screen for global factory configuration.

    Returns ``"saved"`` when settings are explicitly saved,
    or ``None`` on back / quit.
    """

    TITLE = "Settings — Configure Factory"
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("m", "change_model", "Model"),
        Binding("p", "change_provider", "Provider"),
        Binding("u", "cycle_auto_update", "Auto-update"),
        Binding("o", "toggle_ouroboros", "Ouroboros"),
        Binding("d", "change_dashboard", "Dashboard"),
        Binding("s", "save_settings", "Save"),
        Binding("q", "quit", "Quit"),
    ]
    CSS = _SETTINGS_CSS

    def __init__(
        self,
        settings: SettingsModel | None = None,
        config_root: Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config_root = config_root
        if settings is not None:
            self._settings = settings
        else:
            self._settings = load_settings(config_root)
        self._dirty = False

    @property
    def settings(self) -> SettingsModel:
        """Return the current settings snapshot."""
        return self._settings

    @property
    def dirty(self) -> bool:
        """Return whether settings have unsaved changes."""
        return self._dirty

    # ── Compose ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            SettingsBanner(id="settings-banner"),
            DataTable(id="settings-table"),
            Label("", id="save-indicator"),
            SettingsStatusBar(id="settings-status-bar"),
        )
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────

    def on_mount(self) -> None:
        """Apply Settings theme and populate the settings table."""
        apply_subsystem_theme(self, "settings")
        self._setup_table()
        self._refresh_table()

    def _setup_table(self) -> None:
        """Add columns to the settings DataTable."""
        table: DataTable[Any] = self.query_one("#settings-table", DataTable)
        table.add_columns("Setting", "Value", "Key")
        table.cursor_type = "row"

    def _refresh_table(self) -> None:
        """Populate the settings table from current model."""
        table: DataTable[Any] = self.query_one("#settings-table", DataTable)
        table.clear()
        s = self._settings
        table.add_row(
            "Model",
            s.model or f"[{THEME.text_muted}](not set)[/{THEME.text_muted}]",
            "[m]",
        )
        table.add_row(
            "Provider",
            s.provider,
            "[p]",
        )
        table.add_row(
            "Auto-update Policy",
            _format_auto_update(s.auto_update),
            "[u]",
        )
        table.add_row(
            "Ouroboros Self-forge",
            _format_bool(s.ouroboros_self_forge),
            "[o]",
        )
        table.add_row(
            "Dashboard Refresh (s)",
            str(s.dashboard_refresh_interval),
            "[d]",
        )

    def _update_settings(self, **kwargs: Any) -> None:
        """Replace settings with updated fields and refresh display."""
        from dataclasses import asdict  # noqa: PLC0415

        current = asdict(self._settings)
        current.update(kwargs)
        self._settings = SettingsModel(**current)
        self._dirty = True
        self._refresh_table()
        self.query_one("#save-indicator", Label).update("")

    # ── Actions ───────────────────────────────────────────────

    def action_change_model(self) -> None:
        """Cycle through example model names."""
        models = [
            "",
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-haiku-4-20250514",
        ]
        current_idx = 0
        for i, m in enumerate(models):
            if m == self._settings.model:
                current_idx = i
                break
        next_idx = (current_idx + 1) % len(models)
        self._update_settings(model=models[next_idx])

    def action_change_provider(self) -> None:
        """Cycle through provider options."""
        providers = ["anthropic", "openai", "local"]
        current_idx = 0
        for i, p in enumerate(providers):
            if p == self._settings.provider:
                current_idx = i
                break
        next_idx = (current_idx + 1) % len(providers)
        self._update_settings(provider=providers[next_idx])

    def action_cycle_auto_update(self) -> None:
        """Cycle auto-update policy: enabled → disabled → prompt."""
        current_idx = 0
        for i, policy in enumerate(AUTO_UPDATE_CYCLE):
            if policy == self._settings.auto_update:
                current_idx = i
                break
        next_idx = (current_idx + 1) % len(AUTO_UPDATE_CYCLE)
        self._update_settings(auto_update=AUTO_UPDATE_CYCLE[next_idx])

    def action_toggle_ouroboros(self) -> None:
        """Toggle Ouroboros self-forge on/off."""
        self._update_settings(
            ouroboros_self_forge=not self._settings.ouroboros_self_forge,
        )

    def action_change_dashboard(self) -> None:
        """Cycle through dashboard refresh interval options."""
        intervals = [1.0, 2.0, 5.0, 10.0]
        current_idx = 0
        for i, iv in enumerate(intervals):
            if iv == self._settings.dashboard_refresh_interval:
                current_idx = i
                break
        next_idx = (current_idx + 1) % len(intervals)
        self._update_settings(dashboard_refresh_interval=intervals[next_idx])

    def action_save_settings(self) -> None:
        """Persist current settings to .dark-factory/config.json."""
        cfg = load_config(self._config_root)
        apply_settings_to_config(cfg, self._settings)
        save_config(cfg)
        self._dirty = False
        self.query_one("#save-indicator", Label).update(
            f"[{THEME.success}]\u2714 Settings saved[/{THEME.success}]"
        )

    def action_go_back(self) -> None:
        """Handle [Escape] — return to main menu."""
        self.exit(None)


def run_settings_tui(
    settings: SettingsModel | None = None,
    config_root: Path | None = None,
) -> str | None:
    """Launch the Settings screen.

    Returns
    -------
    str | None
        ``"saved"`` if settings were saved, or ``None`` on back/quit.
    """
    app = SettingsScreen(settings=settings, config_root=config_root)
    return app.run()
