"""Pipeline flow diagram widgets — horizontal node+connector layout."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from textual.app import ComposeResult
from textual.widgets import Static

from dark_factory.ui.theme import STAGE_ICONS, THEME
from dark_factory.ui.widgets.elapsed_timer import ElapsedTimer, _format_elapsed
from dark_factory.ui.widgets.spinner import AnimatedSpinner

if TYPE_CHECKING:
    from textual.timer import Timer

_VALID_STATES = frozenset({"pending", "running", "passed", "failed", "skipped"})

_STATE_ICON: dict[str, str] = {
    "pending": STAGE_ICONS.get("pending", "\u2504"),
    "running": STAGE_ICONS.get("running", "\u25b6"),
    "passed": STAGE_ICONS.get("passed", "\u2714"),
    "failed": STAGE_ICONS.get("failed", "\u2718"),
    "skipped": STAGE_ICONS.get("skipped", "\u2500"),
}


# ── Protocols ────────────────────────────────────────────────────


@runtime_checkable
class PipelineNodeProtocol(Protocol):
    """Protocol for PipelineNode conformance."""

    @property
    def stage_name(self) -> str: ...
    def update_state(self, state: str) -> None: ...
    def update_elapsed(self, elapsed_ms: float) -> None: ...
    def render_content(self) -> str: ...


@runtime_checkable
class PipelineConnectorProtocol(Protocol):
    """Protocol for PipelineConnector conformance."""

    @property
    def completed(self) -> bool: ...
    def set_completed(self, completed: bool) -> None: ...
    def render_content(self) -> str: ...


@runtime_checkable
class PipelineFlowDiagramProtocol(Protocol):
    """Protocol for PipelineFlowDiagram conformance."""

    def refresh_stages(self, stages: list[Any]) -> None: ...
    def start_animation_timer(self) -> None: ...
    def stop_animation_timer(self) -> None: ...


# ── Widgets ──────────────────────────────────────────────────────


class PipelineNode(Static):
    """A single pipeline stage rendered as a Unicode box-drawing node.

    Visual states: pending (dim), running (bright+pulse), passed (green+check),
    failed (red+X), skipped (gray+strikethrough).
    """

    def __init__(self, stage_name: str = "", **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self._stage_name = stage_name
        self._state = "pending"
        self._elapsed_ms: float = 0.0
        # Apply initial CSS class before mount
        self.add_class("-pending")

    @property
    def stage_name(self) -> str:
        """Return the stage name."""
        return self._stage_name

    def compose(self) -> ComposeResult:
        yield Static("", id="node-box")
        yield ElapsedTimer(id="node-timer")
        yield AnimatedSpinner(id="node-spinner")

    def on_mount(self) -> None:
        self._apply_state()
        try:
            self.query_one("#node-spinner", AnimatedSpinner).display = False
            self.query_one("#node-timer", ElapsedTimer).display = False
        except Exception:  # noqa: BLE001
            pass

    def update_state(self, state: str) -> None:
        """Change the visual state of this node."""
        if state not in _VALID_STATES:
            state = "pending"
        old = self._state
        self._state = state
        if old != state:
            self._apply_state()

    def update_elapsed(self, elapsed_ms: float) -> None:
        """Update the elapsed time displayed under this node."""
        self._elapsed_ms = elapsed_ms
        try:
            timer = self.query_one("#node-timer", ElapsedTimer)
            timer.update(elapsed_ms)
        except Exception:  # noqa: BLE001
            pass

    def render_content(self) -> str:
        """Return the current text content of this node for testing."""
        icon = _STATE_ICON.get(self._state, "?")
        name = self._stage_name[:10]
        content = (
            f"\u256d\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256e\n"
            f"\u2502 {icon} {name:<9}\u2502\n"
            f"\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f"
        )
        elapsed_str = _format_elapsed(self._elapsed_ms)
        return content + f"\n{elapsed_str}"

    def _apply_state(self) -> None:
        """Rebuild the box display and CSS classes for the current state."""
        for cls in _VALID_STATES:
            self.remove_class(f"-{cls}")
        self.add_class(f"-{self._state}")

        icon = _STATE_ICON.get(self._state, "?")
        name = self._stage_name[:10]

        box = (
            f"\u256d\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256e\n"
            f"\u2502 {icon} {name:<9}\u2502\n"
            f"\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f"
        )

        try:
            self.query_one("#node-box", Static).update(box)
            spinner = self.query_one("#node-spinner", AnimatedSpinner)
            timer = self.query_one("#node-timer", ElapsedTimer)
            if self._state == "running":
                spinner.display = True
                timer.display = True
            else:
                spinner.display = False
                if self._state in ("passed", "failed"):
                    timer.display = True
                else:
                    timer.display = False
                    timer.reset()
        except Exception:  # noqa: BLE001
            pass


class PipelineConnector(Static):
    """Arrow connector between pipeline nodes.

    Dim when upstream is not complete; bright when upstream has completed.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__("\u2500\u2500\u2500>", **kwargs)
        self._completed = False
        self.add_class("-pending")

    @property
    def completed(self) -> bool:
        """Return whether upstream is complete."""
        return self._completed

    def set_completed(self, completed: bool) -> None:
        """Update the connector appearance based on upstream completion."""
        self._completed = completed
        if completed:
            self.remove_class("-pending")
            self.add_class("-completed")
        else:
            self.remove_class("-completed")
            self.add_class("-pending")

    def render_content(self) -> str:
        """Return the connector arrow string."""
        return "\u2500\u2500\u2500>"

    def on_mount(self) -> None:
        self.add_class("-pending")


class PipelineFlowDiagram(Static):
    """Horizontal flow diagram composing PipelineNodes and PipelineConnectors.

    Replaces the DataTable-based pipeline panel.  The ``refresh_stages()``
    method has the same signature as the old ``PipelinePanel.refresh_stages()``.
    """

    _STAGE_NAMES: tuple[str, ...] = (
        "Sentinel", "Dark Forge", "Crucible", "Gate", "Deploy", "Verify",
    )

    def __init__(self, **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self._stage_nodes: list[PipelineNode] = []
        self._stage_connectors: list[PipelineConnector] = []
        self._animation_timer: Timer | None = None
        self._prev_states: list[str] = []
        self._render_count: int = 0
        self._headless = os.environ.get("DARK_FACTORY_HEADLESS") == "1"

    def compose(self) -> ComposeResult:
        # Nodes are created dynamically in refresh_stages()
        return
        yield  # make this a generator

    def on_mount(self) -> None:
        pass

    def _ensure_nodes(self, count: int, stages: list[Any]) -> None:
        """Create/remove nodes to match the number of stages."""
        current = len(self._stage_nodes)
        if current == count:
            return
        # Remove existing nodes
        for node in self._stage_nodes:
            if node.parent is not None:
                try:
                    node.parent._nodes._remove(node)  # type: ignore[union-attr]
                except (ValueError, AttributeError):
                    pass
                node.remove()
        for conn in self._stage_connectors:
            if conn.parent is not None:
                try:
                    conn.parent._nodes._remove(conn)  # type: ignore[union-attr]
                except (ValueError, AttributeError):
                    pass
                conn.remove()
        self._stage_nodes.clear()
        self._stage_connectors.clear()
        # Create new nodes
        widgets = []
        for i in range(count):
            name = stages[i].name if hasattr(stages[i], "name") else (
                self._STAGE_NAMES[i] if i < len(self._STAGE_NAMES) else f"Stage {i}"
            )
            node = PipelineNode(stage_name=name, id=f"stage-node-{i}")
            self._stage_nodes.append(node)
            widgets.append(node)
            if i < count - 1:
                conn = PipelineConnector(id=f"stage-conn-{i}")
                self._stage_connectors.append(conn)
                widgets.append(conn)
        if widgets:
            self.mount(*widgets)

    def start_animation_timer(self) -> None:
        """Start the shared animation timer (~200ms)."""
        if self._animation_timer is None:
            self._animation_timer = self.set_interval(0.2, self._on_tick)

    def stop_animation_timer(self) -> None:
        """Stop and clear the animation timer."""
        if self._animation_timer is not None:
            self._animation_timer.stop()
            self._animation_timer = None

    def _on_tick(self) -> None:
        """Single shared animation timer (~200ms)."""
        for node in self._stage_nodes:
            if node._state == "running":
                try:
                    node.query_one("#node-spinner", AnimatedSpinner).tick()
                except Exception:  # noqa: BLE001
                    pass

    def refresh_stages(self, stages: list[Any]) -> None:
        """Update the flow diagram from a list of StageStatus objects.

        Dirty-flag: only updates nodes whose state actually changed.
        """
        new_states = [
            s.state if hasattr(s, "state") else str(s) for s in stages
        ]

        # Dirty-flag optimization: skip if all states are identical
        if new_states == self._prev_states:
            return

        self._render_count += 1

        # Ensure correct number of nodes
        if len(self._stage_nodes) != len(stages):
            self._ensure_nodes(len(stages), stages)

        for i, stage in enumerate(stages):
            if i >= len(self._stage_nodes):
                break

            state = stage.state if hasattr(stage, "state") else str(stage)
            name = stage.name if hasattr(stage, "name") else self._STAGE_NAMES[i] if i < len(self._STAGE_NAMES) else ""
            elapsed = stage.duration_ms if hasattr(stage, "duration_ms") else 0.0

            old_state = self._prev_states[i] if i < len(self._prev_states) else ""
            if state == old_state and state != "running":
                continue

            node = self._stage_nodes[i]
            node._stage_name = name[:10]
            node.update_state(state)
            node.update_elapsed(elapsed)

        # Update connectors
        for i, conn in enumerate(self._stage_connectors):
            if i < len(stages):
                upstream_state = stages[i].state if hasattr(stages[i], "state") else ""
                conn.set_completed(upstream_state in ("passed", "failed", "skipped"))

        # Auto-start/stop timer based on running state
        any_running = "running" in new_states
        if any_running and self._animation_timer is None and not self._headless:
            self.start_animation_timer()
        elif not any_running and self._animation_timer is not None:
            self.stop_animation_timer()

        self._prev_states = new_states
