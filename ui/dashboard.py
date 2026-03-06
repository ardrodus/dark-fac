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

from dark_factory.ui.notifications import Notification, NotificationPanel, get_store
from dark_factory.ui.theme import (
    COMPACT_ICONS,
    FULL_HEADER_BANNER,
    PILLARS,
    THEME,
    build_css,
    stage_icon,
)

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


@dataclass(frozen=True, slots=True)
class ObeliskInvestigation:
    """Compact investigation record for dashboard display."""

    id: str
    verdict: str
    timestamp: float
    url: str = ""


@dataclass(frozen=True, slots=True)
class ObeliskStatus:
    """Obelisk supervisor status snapshot read from obelisk-status.json."""

    status: str = "unknown"  # watching | investigating | crashed | crash_loop | stopped | unknown
    dark_factory_pid: int | None = None
    uptime_s: float = 0.0
    crash_count: int = 0
    investigations: tuple[ObeliskInvestigation, ...] = ()


@dataclass(slots=True)
class DashboardState:
    """Mutable aggregate state consumed by the dashboard widgets."""

    stages: list[StageStatus] = field(default_factory=list)
    agents: list[AgentInfo] = field(default_factory=list)
    health: list[HealthStatus] = field(default_factory=list)
    gate_summaries: list[GateSummary] = field(default_factory=list)
    notifications: tuple[Notification, ...] = ()
    obelisk: ObeliskStatus = field(default_factory=ObeliskStatus)
    queue_depth: int = 0
    refresh_interval: float = 2.0
    human_gate_queue: Any = None  # Optional HumanGateQueue instance


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
    # Obelisk supervisor states
    "watching": THEME.success,
    "investigating": THEME.info,
    "crashed": THEME.error,
    "crash_loop": THEME.error,
    "stopped": THEME.text_muted,
    "unknown": THEME.warning,
}


def _colour_for(state: str) -> str:
    return _STATE_COLOUR.get(state, THEME.text)


# ── Widgets ───────────────────────────────────────────────────────


class BannerPanel(Static):
    """Header banner with ASCII art and pillar subtitle bar."""

    def compose(self) -> ComposeResult:
        yield Static(FULL_HEADER_BANNER, id="banner-content")


class PipelinePanel(Static):
    """Pipeline-stage progress table with a progress bar."""

    def compose(self) -> ComposeResult:
        yield Label(f"[b][{PILLARS.dark_forge}]{COMPACT_ICONS['dark_forge']}[/] Pipeline Stages[/b]")
        yield DataTable(id="stage-table")
        yield ProgressBar(id="stage-progress", total=100, show_eta=False)

    def on_mount(self) -> None:
        table: DataTable[Any] = self.query_one("#stage-table", DataTable)
        table.add_columns("", "Stage", "State", "Detail", "Time (ms)")
        table.cursor_type = "none"

    def refresh_stages(self, stages: list[StageStatus]) -> None:
        """Replace all rows in the stage table and update the progress bar."""
        table: DataTable[Any] = self.query_one("#stage-table", DataTable)
        table.clear()
        completed = 0
        for s in stages:
            colour = _colour_for(s.state)
            icon = stage_icon(s.state)
            table.add_row(
                f"[{colour}]{icon}[/]",
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
        yield Label(f"[b][{PILLARS.ouroboros}]{COMPACT_ICONS['ouroboros']}[/] Agent Activity[/b]")
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
        yield Label(f"[b][{PILLARS.obelisk}]{COMPACT_ICONS['obelisk']}[/] System Health[/b]")
        yield Horizontal(
            DataTable(id="health-table"),
            Vertical(
                Label("Queue depth"),
                Label("0", id="queue-label"),
            ),
        )
        yield Label(f"[b][{PILLARS.obelisk}]{COMPACT_ICONS['obelisk']}[/] Obelisk Supervisor[/b]")
        yield Label("", id="obelisk-summary")
        yield DataTable(id="obelisk-inv-table")

    def on_mount(self) -> None:
        table: DataTable[Any] = self.query_one("#health-table", DataTable)
        table.add_columns("Component", "Healthy", "Detail")
        table.cursor_type = "none"

        inv_table: DataTable[Any] = self.query_one("#obelisk-inv-table", DataTable)
        inv_table.add_columns("Investigation", "Verdict", "URL")
        inv_table.cursor_type = "none"

    def refresh_health(
        self,
        health: list[HealthStatus],
        queue_depth: int,
        obelisk: ObeliskStatus | None = None,
    ) -> None:
        """Update health rows, queue depth, and Obelisk supervisor status."""
        table: DataTable[Any] = self.query_one("#health-table", DataTable)
        table.clear()
        for h in health:
            colour = THEME.success if h.healthy else THEME.error
            icon = "\u2714" if h.healthy else "\u2718"
            table.add_row(h.component, f"[{colour}]{icon}[/]", h.detail[:40])
        self.query_one("#queue-label", Label).update(str(queue_depth))

        # Obelisk supervisor section
        if obelisk is None:
            obelisk = ObeliskStatus()
        state_colour = _colour_for(obelisk.status)
        pid_str = str(obelisk.dark_factory_pid) if obelisk.dark_factory_pid else "-"
        uptime_m = int(obelisk.uptime_s // 60)
        summary = (
            f"  State: [{state_colour}]{obelisk.status}[/]"
            f"  |  PID: {pid_str}"
            f"  |  Uptime: {uptime_m}m"
            f"  |  Crashes: {obelisk.crash_count}"
        )
        self.query_one("#obelisk-summary", Label).update(summary)

        inv_table: DataTable[Any] = self.query_one("#obelisk-inv-table", DataTable)
        inv_table.clear()
        for inv in obelisk.investigations[-5:]:
            v_colour = THEME.success if inv.verdict == "FIXED" else THEME.warning
            inv_table.add_row(inv.id, f"[{v_colour}]{inv.verdict}[/]", inv.url or "-")


class GatePanel(Static):
    """Gate summary table showing pass/fail status for each gate."""

    def compose(self) -> ComposeResult:
        yield Label(f"[b][{PILLARS.sentinel}]{COMPACT_ICONS['sentinel']}[/] Gate Summary[/b]")
        yield DataTable(id="gate-table")

    def on_mount(self) -> None:
        table: DataTable[Any] = self.query_one("#gate-table", DataTable)
        table.add_columns("Gate", "Status", "Checks", "Detail")
        table.cursor_type = "none"

    def refresh_gates(
        self,
        gate_summaries: list[GateSummary],
        human_gate_queue: Any = None,
    ) -> None:
        """Replace all rows in the gate table, including pending human gates."""
        table: DataTable[Any] = self.query_one("#gate-table", DataTable)
        table.clear()
        # Pending human gates first (user attention needed)
        if human_gate_queue is not None:
            for req in human_gate_queue.pending:
                table.add_row(
                    f"{req.gate_type} #{req.issue_number}",
                    f"[{THEME.warning}]\u23f3 PENDING[/]",
                    "[a]pprove / [x]reject",
                    req.title[:50],
                )
        for gs in gate_summaries:
            colour = THEME.success if gs.passed else THEME.error
            icon = "\u2714 PASS" if gs.passed else "\u2718 FAIL"
            table.add_row(
                gs.name,
                f"[{colour}]{icon}[/]",
                str(gs.check_count),
                gs.detail[:50],
            )


class LogPanel(Static):
    """Scrollable live-log area backed by :class:`RichLog`."""

    def compose(self) -> ComposeResult:
        yield Label("[b]\u25b6 Live Logs[/b]")
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
        Binding("a", "approve_gate", "Approve Gate"),
        Binding("x", "reject_gate", "Reject Gate"),
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
            BannerPanel(id="banner-panel"),
            PipelinePanel(id="pipeline-panel"),
            Horizontal(
                AgentPanel(id="agent-panel"),
                HealthPanel(id="health-panel"),
            ),
            GatePanel(id="gate-panel"),
            NotificationPanel(id="notification-panel"),
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

    def _respond_to_first_gate(self, approved: bool) -> None:
        """Resolve the first pending human gate if one exists."""
        queue = self._state.human_gate_queue
        if queue is None or not queue.pending:
            return
        from dark_factory.gates.human_gate import HumanGateResponse  # noqa: PLC0415

        request = queue.pending[0]
        action = "Approved" if approved else "Rejected"
        queue.respond(request, HumanGateResponse(approved=approved))
        self.log_message(f"[bold]{action}[/bold] gate: {request.gate_type} #{request.issue_number}")
        self._repaint()

    def action_approve_gate(self) -> None:
        """Bound to the ``a`` key — approve the first pending human gate."""
        self._respond_to_first_gate(approved=True)

    def action_reject_gate(self) -> None:
        """Bound to the ``x`` key — reject the first pending human gate."""
        self._respond_to_first_gate(approved=False)

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
                obelisk=self._state.obelisk,
            )
            self.query_one("#gate-panel", GatePanel).refresh_gates(
                self._state.gate_summaries,
                human_gate_queue=self._state.human_gate_queue,
            )
            notifs = self._state.notifications or get_store().items
            self.query_one("#notification-panel", NotificationPanel).refresh_notifications(
                notifs,
            )
        except Exception:  # noqa: BLE001
            # Widgets may not be mounted yet during early startup.
            logger.debug("Repaint skipped -- widgets not ready")
