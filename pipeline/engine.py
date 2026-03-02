"""FactoryPipelineEngine -- high-level wrapper integrating the engine with Dark Factory.

Bridges Dark Factory's config system, pipeline loader, and DOT-based engine
into a single class with convenience methods for each subsystem:
Dark Forge, Sentinel, Crucible, and Ouroboros.

Usage::

    from factory.pipeline.engine import FactoryPipelineEngine

    engine = FactoryPipelineEngine()                    # uses .dark-factory/config.json
    result = await engine.run_pipeline("dark_forge", {"issue": issue_json})
    result = await engine.run_sentinel_gate(1, "/path/to/workspace")
    result = await engine.run_forge(issue_json, "/path/to/workspace")
    result = await engine.run_crucible("/path/to/workspace", "abc123", "def456")
    result = await engine.run_ouroboros("scheduled")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.engine.events import PipelineEvent

logger = logging.getLogger(__name__)

# Gate numbers mapped to the sentinel DOT entry-point node IDs.
_SENTINEL_GATE_ENTRIES: dict[int, str] = {
    1: "gate1_start",
    2: "gate2_start",
    3: "gate3_start",
    4: "gate4_start",
    5: "gate5_start",
}


class FactoryPipelineEngine:
    """Wrapper that integrates the ported engine with Dark Factory's config and backend.

    Constructor reads ``EngineConfig`` from ``.dark-factory/config.json``,
    initialises a ``ClaudeCodeBackend``, and provides convenience methods
    for running named pipelines and each Dark Factory subsystem.
    """

    def __init__(
        self,
        *,
        config_start: Path | None = None,
        on_event: Callable[[PipelineEvent], None] | None = None,
    ) -> None:
        from factory.engine.claude_backend import (  # noqa: PLC0415
            ClaudeCodeBackend,
            ClaudeCodeConfig,
        )
        from factory.engine.config import load_engine_config  # noqa: PLC0415

        self._engine_cfg = load_engine_config(config_start)
        self._backend = ClaudeCodeBackend(
            ClaudeCodeConfig(
                model=self._engine_cfg.model,
                claude_path=self._engine_cfg.claude_path,
            )
        )
        self._config_start = config_start
        self._on_event = on_event

    # ── Generic pipeline execution ───────────────────────────────

    async def run_pipeline(
        self,
        name: str,
        context: dict[str, Any] | None = None,
        *,
        on_event: Callable[[PipelineEvent], None] | None = None,
    ) -> Any:
        """Run any named pipeline with the given context.

        Resolves *name* via the pipeline loader (built-in, user override,
        or config override), injects the ``strategy`` variable from
        ``EngineConfig.deploy_strategy``, then delegates to
        :func:`factory.engine.sdk.execute`.

        Args:
            name: Pipeline stem name (e.g. ``"dark_forge"``, ``"sentinel"``).
            context: Variables available to pipeline nodes via ``$variable``.
            on_event: Per-call event callback (overrides constructor default).

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        from factory.engine.sdk import execute  # noqa: PLC0415
        from factory.pipeline.loader import discover_pipelines  # noqa: PLC0415

        pipelines = discover_pipelines(project_root=self._config_start)
        dotfile = pipelines.get(name)
        if dotfile is None:
            msg = (
                f"Pipeline '{name}' not found. "
                f"Available: {sorted(pipelines)}"
            )
            raise FileNotFoundError(msg)

        # Inject strategy so arch_review_${strategy}.dot resolves correctly.
        ctx = dict(context or {})
        ctx.setdefault("strategy", self._engine_cfg.deploy_strategy)

        event_cb = on_event or self._on_event
        return await execute(
            dotfile,
            model=self._engine_cfg.model or None,
            context=ctx,
            on_event=event_cb,
        )

    # ── Sentinel gates ───────────────────────────────────────────

    async def run_sentinel_gate(
        self,
        gate: int,
        workspace: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run a specific Sentinel gate (1-5) via the sentinel.dot pipeline.

        The sentinel pipeline is loaded and executed starting from the
        gate's entry node.

        Args:
            gate: Gate number (1-5).
            workspace: Path to the workspace under scan.
            context: Extra context variables for the gate.

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        if gate not in _SENTINEL_GATE_ENTRIES:
            msg = f"Invalid sentinel gate: {gate}. Must be 1-5."
            raise ValueError(msg)

        ctx: dict[str, Any] = {"workspace": workspace, **(context or {})}
        ctx.setdefault("strategy", self._engine_cfg.deploy_strategy)

        from factory.engine.sdk import execute  # noqa: PLC0415
        from factory.pipeline.loader import discover_pipelines  # noqa: PLC0415

        pipelines = discover_pipelines(project_root=self._config_start)
        dotfile = pipelines.get("sentinel")
        if dotfile is None:
            msg = "sentinel pipeline not found"
            raise FileNotFoundError(msg)

        return await execute(
            dotfile,
            model=self._engine_cfg.model or None,
            context=ctx,
            on_event=self._on_event,
        )

    # ── Dark Forge ───────────────────────────────────────────────

    async def run_forge(
        self,
        issue: dict[str, Any],
        workspace: str,
        *,
        strategy: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run Dark Forge with issue JSON, workspace, and strategy.

        Args:
            issue: Issue data (number, title, body, labels, etc.).
            workspace: Path to the workspace directory.
            strategy: Deploy strategy override (``"web"`` or ``"console"``).
                Defaults to ``EngineConfig.deploy_strategy``.
            context: Extra context variables.

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        ctx: dict[str, Any] = {
            "issue": issue,
            "workspace": workspace,
            **(context or {}),
        }
        ctx["strategy"] = strategy or self._engine_cfg.deploy_strategy
        return await self.run_pipeline("dark_forge", ctx)

    # ── Crucible ─────────────────────────────────────────────────

    async def run_crucible(
        self,
        workspace: str,
        base_sha: str,
        head_sha: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run Crucible with the given SHAs.

        Args:
            workspace: Path to the workspace directory.
            base_sha: Base commit SHA for diff comparison.
            head_sha: Head commit SHA for diff comparison.
            context: Extra context variables.

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        ctx: dict[str, Any] = {
            "workspace": workspace,
            "base_sha": base_sha,
            "head_sha": head_sha,
            **(context or {}),
        }
        return await self.run_pipeline("crucible", ctx)

    # ── Ouroboros ─────────────────────────────────────────────────

    async def run_ouroboros(
        self,
        trigger: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run Ouroboros with the given trigger type.

        Args:
            trigger: Trigger type (``"scheduled"``, ``"post_cycle"``,
                ``"manual"``, ``"auto_update"``, ``"self_forge"``).
            context: Extra context variables.

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        ctx: dict[str, Any] = {
            "trigger": trigger,
            **(context or {}),
        }
        return await self.run_pipeline("ouroboros", ctx)
