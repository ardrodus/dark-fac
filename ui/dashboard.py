"""Interactive console dashboard for the Dark Factory pipeline.

Built with Textual — a Python TUI framework with reactive widgets, layout,
and CSS-like styling.  This module replaces any previous ANSI-based console
output with first-class Textual widgets:

* :class:`~textual.widgets.DataTable` for pipeline-stage metrics
* :class:`~textual.widgets.RichLog` for live log streaming
* :class:`~textual.widgets.ProgressBar` for pipeline-stage progress
* :class:`~textual.widgets.Header` / :class:`~textual.widgets.Footer` for
  navigation chrome
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, ProgressBar, RichLog, Static

from factory.ui.theme import THEME, build_css

if TYPE_CHECKING:
    from textual.timer import Timer

logger = logging.getLogger(__name__)

# ── Data models ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StageStatus:
    """Snapshot of a single pipeline stage's status."""

    name: str
    state: str  # "pending" | "running" | "passed" | "failed" | "skipped"
    detail: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class AgentInfo:
    """Snapshot of an agent's current activity."""

    role: str
    status: str  # "idle" | "active" | "error"
    task: str = ""


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Obelisk / infrastructure health snapshot."""

    component: str
    healthy: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GateSummary:
    """Summary of a single gate's results for dashboard display."""

    name: str
    passed: bool
    check_count: int
    detail: str = ""


@dataclass(slots=True)
class DashboardState:
    """Mutable aggregate state consumed by the dashboard widgets."""

    stages: list[StageStatus] = field(default_factory=list)
    agents: list[AgentInfo] = field(default_factory=list)
    health: list[HealthStatus] = field(default_factory=list)
    gate_summaries: list[GateSummary] = field(default_factory=list)
    queue_depth: int = 0
    refresh_interval: float = 2.0


# ── Helper: state → colour ────────────────────────────────────────

_STATE_COLOUR: dict[str, str] = {
    "pending": THEME.text_muted,
    "running": THEME.info,
    "passed": THEME.success,
    "failed": THEME.error,
    "skipped": THEME.warning,
    "idle": THEME.text_muted,
    "active": THEME.success,
    "error": THEME.error,
}


def _colour_for(state: str) -> str:
    return _STATE_COLOUR.get(state, THEME.text)


# ── Widgets ───────────────────────────────────────────────────────


class PipelinePanel(Static):
    """Pipeline-stage progress table with a progress bar."""

    def compose(self) -> ComposeResult:
        yield Label("[b]Pipeline Stages[/b]")
        yield DataTable(id="stage-table")
        yield ProgressBar(id="stage-progress", total=100, show_eta=False)

    def on_mount(self) -> None:
        table: DataTable[Any] = self.query_one("#stage-table", DataTable)
        table.add_columns("Stage", "State", "Detail", "Time (ms)")
        table.cursor_type = "none"

    def refresh_stages(self, stages: list[StageStatus]) -> None:
        """Replace all rows in the stage table and update the progress bar."""
        table: DataTable[Any] = self.query_one("#stage-table", DataTable)
        table.clear()
        completed = 0
        for s in stages:
            colour = _colour_for(s.state)
            table.add_row(
                s.name,
                f"[{colour}]{s.state}[/]",
                s.detail[:60],
                f"{s.duration_ms:.1f}",
            )
            if s.state in ("passed", "failed", "skipped"):
                completed += 1
        bar = self.query_one("#stage-progress", ProgressBar)
        pct = int(completed / max(len(stages), 1) * 100)
        bar.update(progress=pct)


class AgentPanel(Static):
    """Table showing current agent activity."""

    def compose(self) -> ComposeResult:
        yield Label("[b]Agent Activity[/b]")
        yield DataTable(id="agent-table")

    def on_mount(self) -> None:
        table: DataTable[Any] = self.query_one("#agent-table", DataTable)
        table.add_columns("Role", "Status", "Task")
        table.cursor_type = "none"

    def refresh_agents(self, agents: list[AgentInfo]) -> None:
        """Replace all rows in the agent table."""
        table: DataTable[Any] = self.query_one("#agent-table", DataTable)
        table.clear()
        for a in agents:
            colour = _colour_for(a.status)
            table.add_row(a.role, f"[{colour}]{a.status}[/]", a.task[:50])


class HealthPanel(Static):
    """Obelisk / infrastructure health and queue depth."""

    def compose(self) -> ComposeResult:
        yield Label("[b]System Health[/b]")
        yield Horizontal(
            DataTable(id="health-table"),
            Vertical(
                Label("Queue depth"),
                Label("0", id="queue-label"),
            ),
        )

    def on_mount(self) -> None:
        table: DataTable[Any] = self.query_one("#health-table", DataTable)
        table.add_columns("Component", "Healthy", "Detail")
        table.cursor_type = "none"

    def refresh_health(
        self, health: list[HealthStatus], queue_depth: int,
    ) -> None:
        """Update health rows and queue depth label."""
        table: DataTable[Any] = self.query_one("#health-table", DataTable)
        table.clear()
        for h in health:
            colour = THEME.success if h.healthy else THEME.error
            icon = "OK" if h.healthy else "FAIL"
            table.add_row(h.component, f"[{colour}]{icon}[/]", h.detail[:40])
        self.query_one("#queue-label", Label).update(str(queue_depth))


class GatePanel(Static):
    """Gate summary table showing pass/fail status for each gate."""

    def compose(self) -> ComposeResult:
        yield Label("[b]Gate Summary[/b]")
        yield DataTable(id="gate-table")

    def on_mount(self) -> None:
        table: DataTable[Any] = self.query_one("#gate-table", DataTable)
        table.add_columns("Gate", "Status", "Checks", "Detail")
        table.cursor_type = "none"

    def refresh_gates(self, gate_summaries: list[GateSummary]) -> None:
        """Replace all rows in the gate table."""
        table: DataTable[Any] = self.query_one("#gate-table", DataTable)
        table.clear()
        for gs in gate_summaries:
            colour = THEME.success if gs.passed else THEME.error
            icon = "PASS" if gs.passed else "FAIL"
            table.add_row(
                gs.name,
                f"[{colour}]{icon}[/]",
                str(gs.check_count),
                gs.detail[:50],
            )


class LogPanel(Static):
    """Scrollable live-log area backed by :class:`RichLog`."""

    def compose(self) -> ComposeResult:
        yield Label("[b]Live Logs[/b]")
        yield RichLog(id="live-log", highlight=True, markup=True, max_lines=500)

    def write_log(self, message: str) -> None:
        """Append a log line."""
        self.query_one("#live-log", RichLog).write(message)


# ── Main application ──────────────────────────────────────────────


class DashboardApp(App[None]):
    """Interactive Dark Factory dashboard.

    Composes widgets for pipeline status, agent activity, Obelisk health,
    queue depth, and live log streaming.
    """

    TITLE = "Dark Factory Dashboard"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "force_refresh", "Refresh"),
    ]
    DEFAULT_CSS = build_css()

    # Reactive state: bump to trigger a repaint.
    tick: reactive[int] = reactive(0)

    def __init__(
        self,
        state: DashboardState | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._state = state or DashboardState()
        self._refresh_timer: Timer | None = None

    # ── Compose ───────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            PipelinePanel(id="pipeline-panel"),
            Horizontal(
                AgentPanel(id="agent-panel"),
                HealthPanel(id="health-panel"),
            ),
            GatePanel(id="gate-panel"),
            LogPanel(id="log-panel"),
        )
        yield Footer()

    # ── Lifecycle ─────────────────────────────────────────────

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(
            self._state.refresh_interval,
            self._on_tick,
        )
        self._repaint()

    def _on_tick(self) -> None:
        self.tick += 1

    def watch_tick(self) -> None:
        """Reactive watcher — repaint widgets whenever tick changes."""
        self._repaint()

    # ── Public API ────────────────────────────────────────────

    @property
    def state(self) -> DashboardState:
        """Return a reference to the mutable dashboard state."""
        return self._state

    def update_state(self, state: DashboardState) -> None:
        """Replace the dashboard state and force a repaint."""
        self._state = state
        self._repaint()

    def log_message(self, message: str) -> None:
        """Append a message to the live-log panel."""
        try:
            self.query_one("#log-panel", LogPanel).write_log(message)
        except Exception:  # noqa: BLE001
            logger.debug("Log panel not mounted yet")

    def action_force_refresh(self) -> None:
        """Bound to the ``r`` key — manually trigger a repaint."""
        self._repaint()

    # ── Internal ──────────────────────────────────────────────

    def _repaint(self) -> None:
        """Push current state into every panel widget."""
        try:
            self.query_one("#pipeline-panel", PipelinePanel).refresh_stages(
                self._state.stages,
            )
            self.query_one("#agent-panel", AgentPanel).refresh_agents(
                self._state.agents,
            )
            self.query_one("#health-panel", HealthPanel).refresh_health(
                self._state.health,
                self._state.queue_depth,
            )
            self.query_one("#gate-panel", GatePanel).refresh_gates(
                self._state.gate_summaries,
            )
        except Exception:  # noqa: BLE001
            # Widgets may not be mounted yet during early startup.
            logger.debug("Repaint skipped — widgets not ready")
