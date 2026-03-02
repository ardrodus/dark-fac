"""Foundry per-workspace configuration screen (US-008).

Drill-in screen showing detailed workspace configuration with actions
to change deploy strategy, Sentinel scan mode, watched branch, webhooks,
deploy pipeline, remove workspace, and re-scan baseline.

Keyboard actions:
- [s] Change deploy strategy
- [m] Change Sentinel scan mode
- [b] Change watched branch
- [w] Configure webhooks
- [p] Configure deploy pipeline
- [d] Remove workspace
- [r] Re-scan baseline
- [Escape] Back to workspace list
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Label, Static

from dark_factory.ui.theme import (
    COMPACT_ICONS,
    PILLARS,
    THEME,
    apply_subsystem_theme,
    build_theme_css,
)

# ── Default deploy pipeline path ─────────────────────────────────

_PIPELINE_DIR = Path(".dark-factory") / "pipelines"
_DEPLOY_DOT = "deploy.dot"

_DEPLOY_PIPELINE_NOT_CONFIGURED = "default (empty - not configured)"


# ── Workspace config model ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkspaceConfig:
    """Full configuration for a single workspace.

    Extends the basic ``Workspace`` model with all fields needed by
    the per-workspace drill-in screen.
    """

    repo: str
    strategy: str = "console"           # "web" | "console"
    scan_mode: str = "full"             # "full" | "fast" | "off"
    watched_branch: str = "main"
    webhook_status: str = "disabled"    # "enabled" | "disabled"
    last_forge_run: str = "never"
    last_crucible_verdict: str = "none"
    deploy_pipeline: str = _DEPLOY_PIPELINE_NOT_CONFIGURED
    status: str = "active"              # "active" | "paused"
    skip_arch_review: bool = False      # skip architecture review pipeline

    # Mutable actions performed via the TUI are communicated back to
    # the caller via the App return value.
    actions_taken: tuple[str, ...] = field(default_factory=tuple)


def has_custom_deploy_dot(workspace_root: Path | None = None) -> bool:
    """Check whether a workspace has a custom deploy.dot override.

    Each workspace can override the default pipeline by placing their
    own ``deploy.dot`` in ``.dark-factory/pipelines/deploy.dot``.
    """
    if workspace_root is None:
        return False
    dot_path = workspace_root / _PIPELINE_DIR / _DEPLOY_DOT
    return dot_path.is_file()


def resolve_deploy_pipeline(workspace_root: Path | None = None) -> str:
    """Return the deploy pipeline description for display.

    Returns the path to the custom ``deploy.dot`` if one exists,
    otherwise the default placeholder.
    """
    if workspace_root is not None:
        dot_path = workspace_root / _PIPELINE_DIR / _DEPLOY_DOT
        if dot_path.is_file():
            return str(dot_path)
    return _DEPLOY_PIPELINE_NOT_CONFIGURED


# ── Widgets ──────────────────────────────────────────────────────


class ConfigBanner(Static):
    """Header banner for the workspace config screen."""

    def __init__(self, repo: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._repo = repo

    def compose(self) -> ComposeResult:
        yield Label(
            f"[bold {THEME.primary_light}]"
            f"\n"
            f"     [{PILLARS.obelisk}]{COMPACT_ICONS['foundry']}[/{PILLARS.obelisk}]  "
            f"Workspace Configuration\n"
            f"[/]"
            f"[{THEME.text_muted}]"
            f"     {self._repo}\n"
            f"[/{THEME.text_muted}]"
        )


class ConfigDetailPanel(Static):
    """Displays the workspace configuration fields as a vertical list."""

    def __init__(self, config: WorkspaceConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        c = self._config
        strategy_color = THEME.info if c.strategy == "web" else THEME.text_accent
        status_color = THEME.success if c.status == "active" else THEME.warning
        scan_color = (
            THEME.success if c.scan_mode == "full"
            else THEME.warning if c.scan_mode == "fast"
            else THEME.error
        )
        webhook_color = THEME.success if c.webhook_status == "enabled" else THEME.text_muted
        verdict_color = (
            THEME.success if c.last_crucible_verdict == "GO"
            else THEME.error if c.last_crucible_verdict == "NO_GO"
            else THEME.text_muted
        )
        pipeline_color = (
            THEME.text_muted
            if c.deploy_pipeline == _DEPLOY_PIPELINE_NOT_CONFIGURED
            else THEME.success
        )

        yield Label(
            f"[{THEME.text}]"
            f"  Deploy Strategy     [{strategy_color}]{c.strategy}[/{strategy_color}]\n"
            f"  Sentinel Scan Mode  [{scan_color}]{c.scan_mode}[/{scan_color}]\n"
            f"  Watched Branch      [{THEME.info}]{c.watched_branch}[/{THEME.info}]\n"
            f"  Webhook Status      [{webhook_color}]{c.webhook_status}[/{webhook_color}]\n"
            f"  Last Forge Run      [{THEME.text_muted}]{c.last_forge_run}[/{THEME.text_muted}]\n"
            f"  Last Crucible       [{verdict_color}]{c.last_crucible_verdict}[/{verdict_color}]\n"
            f"  Deploy Pipeline     [{pipeline_color}]{c.deploy_pipeline}[/{pipeline_color}]\n"
            f"  Status              [{status_color}]{c.status}[/{status_color}]"
            f"[/{THEME.text}]"
        )


class ConfigActionBar(Static):
    """Hint bar showing available keyboard shortcuts."""

    def compose(self) -> ComposeResult:
        yield Label(
            f"[{THEME.text_muted}]"
            f"  [bold]s[/bold] strategy  "
            f"\u2502  [bold]m[/bold] scan mode  "
            f"\u2502  [bold]b[/bold] branch  "
            f"\u2502  [bold]w[/bold] webhooks  "
            f"\u2502  [bold]p[/bold] pipeline  "
            f"\u2502  [bold]d[/bold] remove  "
            f"\u2502  [bold]r[/bold] re-scan  "
            f"\u2502  [bold]Esc[/bold] back"
            f"[/{THEME.text_muted}]"
        )


# ── Main application ─────────────────────────────────────────────

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

#config-detail {{
    height: auto;
    padding: 1 2;
    margin: 0 2;
    background: {THEME.bg_panel};
    border: tall {THEME.border};
}}

#config-action-bar {{
    height: auto;
    padding: 0 2;
    margin: 1 2;
}}
""" + build_theme_css()


class WorkspaceConfigScreen(App[str | None]):
    """Per-workspace configuration screen.

    Shows detailed configuration and provides keyboard actions to
    modify settings.  Returns the action taken as a string (e.g.
    ``"change_strategy"``, ``"remove"``) or ``None`` on back/quit.
    """

    TITLE = "Workspace Configuration"
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("s", "change_strategy", "Strategy"),
        Binding("m", "change_scan_mode", "Scan Mode"),
        Binding("b", "change_branch", "Branch"),
        Binding("w", "configure_webhooks", "Webhooks"),
        Binding("p", "configure_pipeline", "Pipeline"),
        Binding("d", "remove_workspace", "Remove"),
        Binding("r", "rescan_baseline", "Re-scan"),
        Binding("q", "quit", "Quit"),
    ]
    CSS = _CONFIG_CSS

    def __init__(
        self,
        config: WorkspaceConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config

    @property
    def config(self) -> WorkspaceConfig:
        """Return the workspace configuration."""
        return self._config

    # ── Compose ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            ConfigBanner(self._config.repo, id="config-banner"),
            ConfigDetailPanel(self._config, id="config-detail"),
            ConfigActionBar(id="config-action-bar"),
        )
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────

    def on_mount(self) -> None:
        """Apply Foundry theme on mount."""
        apply_subsystem_theme(self, "foundry")

    # ── Actions ───────────────────────────────────────────────

    def action_go_back(self) -> None:
        """Handle [Escape] — return to workspace list."""
        self.exit(None)

    def action_change_strategy(self) -> None:
        """Handle [s] — change deploy strategy."""
        self.exit("change_strategy")

    def action_change_scan_mode(self) -> None:
        """Handle [m] — change Sentinel scan mode."""
        self.exit("change_scan_mode")

    def action_change_branch(self) -> None:
        """Handle [b] — change watched branch."""
        self.exit("change_branch")

    def action_configure_webhooks(self) -> None:
        """Handle [w] — configure webhooks."""
        self.exit("configure_webhooks")

    def action_configure_pipeline(self) -> None:
        """Handle [p] — configure deploy pipeline."""
        self.exit("configure_pipeline")

    def action_remove_workspace(self) -> None:
        """Handle [d] — remove workspace."""
        self.exit("remove")

    def action_rescan_baseline(self) -> None:
        """Handle [r] — re-scan baseline."""
        self.exit("rescan_baseline")


def run_workspace_config_tui(
    config: WorkspaceConfig,
) -> str | None:
    """Launch the per-workspace configuration screen.

    Returns
    -------
    str | None
        The action taken (e.g. ``"change_strategy"``, ``"remove"``),
        or ``None`` on back/quit.
    """
    app = WorkspaceConfigScreen(config=config)
    return app.run()
