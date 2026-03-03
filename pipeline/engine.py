"""FactoryPipelineEngine -- high-level wrapper integrating the engine with Dark Factory.

Bridges Dark Factory's config system, pipeline loader, and DOT-based engine
into a single class with convenience methods for each subsystem:
Dark Forge, Sentinel, Crucible, and Ouroboros.

Usage::

    from dark_factory.pipeline.engine import FactoryPipelineEngine

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

    from dark_factory.engine.events import PipelineEvent

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
        from dark_factory.engine.claude_backend import (  # noqa: PLC0415
            ClaudeCodeBackend,
            ClaudeCodeConfig,
        )
        from dark_factory.engine.config import load_engine_config  # noqa: PLC0415
        from dark_factory.engine.resource_limiter import ResourceLimiter  # noqa: PLC0415

        self._engine_cfg = load_engine_config(config_start)
        self._resource_limiter = ResourceLimiter(
            limit=self._engine_cfg.max_concurrent_subprocesses,
        )
        self._backend = ClaudeCodeBackend(
            ClaudeCodeConfig(
                model=self._engine_cfg.model,
                claude_path=self._engine_cfg.claude_path,
            ),
            resource_limiter=self._resource_limiter,
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
        from dark_factory.engine.sdk import execute  # noqa: PLC0415
        from dark_factory.pipeline.loader import discover_pipelines  # noqa: PLC0415

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

        # Load agent persona files into context so DOT prompts can
        # reference them as $sa_security_console_agent etc.
        self._load_agent_personas(ctx)

        # Derive logs_dir so the runner writes per-node artifacts to disk
        logs_dir = self._derive_logs_dir(name, ctx)

        event_cb = on_event or self._on_event
        return await execute(
            dotfile,
            model=self._engine_cfg.model or None,
            context=ctx,
            logs_dir=logs_dir,
            on_event=event_cb,
        )

    # ── Sentinel gates ───────────────────────────────────────────

    async def run_sentinel_gate(
        self,
        gate: int,
        workspace: str,
        *,
        issue_number: int = 0,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run a specific Sentinel gate (1-5) via the sentinel.dot pipeline.

        The sentinel pipeline is loaded and executed starting from the
        gate's entry node.

        Args:
            gate: Gate number (1-5).
            workspace: Path to the workspace under scan.
            issue_number: Optional issue number for log naming.
            context: Extra context variables for the gate.

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        if gate not in _SENTINEL_GATE_ENTRIES:
            msg = f"Invalid sentinel gate: {gate}. Must be 1-5."
            raise ValueError(msg)

        ctx: dict[str, Any] = {"workspace": workspace, **(context or {})}
        if "strategy" not in ctx and workspace:
            ws_data = self._load_workspace_config(workspace)
            ws_strategy = ws_data.get("strategy", "")
            if ws_strategy:
                ctx["strategy"] = ws_strategy
        ctx.setdefault("strategy", self._engine_cfg.deploy_strategy)

        if issue_number:
            ctx["issue_number"] = str(issue_number)

        # Create workflow log for agent visibility
        if workspace:
            from dark_factory.engine.workflow_log import WorkflowLog  # noqa: PLC0415

            log_name = f"sentinel-gate{gate}-{issue_number}" if issue_number else f"sentinel-gate{gate}"
            wf_log = WorkflowLog(
                Path(workspace) / ".dark-factory" / "logs" / f"workflow-{log_name}.log",
                issue_number=issue_number,
            )
            ctx["_workflow_log"] = str(wf_log.path)

        from dark_factory.engine.sdk import execute  # noqa: PLC0415
        from dark_factory.pipeline.loader import discover_pipelines  # noqa: PLC0415

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

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _load_workspace_config(workspace: str) -> dict[str, Any]:
        """Load workspace-level ``.dark-factory/config.json``.

        Returns the parsed dict, or ``{}`` if the file doesn't exist.
        """
        import json  # noqa: PLC0415

        ws_config_path = Path(workspace) / ".dark-factory" / "config.json"
        if not ws_config_path.is_file():
            return {}
        try:
            return json.loads(ws_config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.debug("Failed to read workspace config at %s", ws_config_path, exc_info=True)
            return {}

    @staticmethod
    def _load_agent_personas(ctx: dict[str, Any]) -> None:
        """Load agent .md files into context for $variable expansion in prompts.

        Scans the ``agents/`` directory next to the ``pipelines/`` directory.
        Each file ``agents/sa-security-console.md`` becomes context key
        ``sa_security_console_agent`` (hyphens to underscores, stem + _agent).
        """
        agents_dir = Path(__file__).resolve().parent.parent / "agents"
        if not agents_dir.is_dir():
            return
        for md_file in agents_dir.glob("*.md"):
            key = md_file.stem.replace("-", "_") + "_agent"
            if key not in ctx:
                try:
                    ctx[key] = md_file.read_text(encoding="utf-8")
                except OSError:
                    pass

    @staticmethod
    def _resolve_crucible_repo(workspace: str) -> str:
        """Look up the crucible test repo from workspace config.

        Raises :class:`FileNotFoundError` if the workspace has no
        ``crucible.test_repo`` configured.
        """
        if not workspace:
            msg = "Cannot resolve crucible repo: no workspace path provided"
            raise FileNotFoundError(msg)

        ws_config_path = Path(workspace) / ".dark-factory" / "config.json"
        data = FactoryPipelineEngine._load_workspace_config(workspace)
        repo = data.get("crucible", {}).get("test_repo", "")
        if not repo:
            msg = (
                f"crucible.test_repo not set in {ws_config_path}. "
                "Run onboarding to configure the crucible test repo."
            )
            raise FileNotFoundError(msg)

        return repo

    @staticmethod
    def _derive_logs_dir(name: str, ctx: dict[str, Any]) -> str | None:
        """Derive a logs directory from workspace + issue context."""
        workspace = ctx.get("workspace", "")
        issue = ctx.get("issue", {})
        issue_num = issue.get("number", 0) if isinstance(issue, dict) else 0
        if workspace and issue_num:
            return str(Path(workspace) / ".dark-factory" / "logs" / f"pipeline-{name}-{issue_num}")
        if workspace:
            return str(Path(workspace) / ".dark-factory" / "logs" / f"pipeline-{name}")
        return None

    @staticmethod
    def _save_pipeline_artifacts(result: Any, workspace: str, issue_num: int) -> None:
        """Persist key pipeline outputs to .dark-factory/specs/{issue}/."""
        if not workspace or not issue_num:
            return
        try:
            from dark_factory.specs.base import save_artifact  # noqa: PLC0415

            state_dir = Path(workspace) / ".dark-factory"

            # Engineering brief (arch_review output)
            brief = result.context.get("codergen.arch_review.output", "")
            if brief:
                save_artifact(brief, "engineering-brief.md", issue_num, state_dir=state_dir)

            # Spec artifacts from generation stages
            spec_map = {
                "gen_prd": "prd.json",
                "gen_design": "design.md",
                "gen_api_contract": "api-contract.yaml",
                "gen_schema": "schema.sql",
                "gen_interfaces": "interfaces.txt",
                "gen_test_strategy": "test-strategy.md",
            }
            for node_id, filename in spec_map.items():
                content = result.context.get(f"codergen.{node_id}.output", "")
                if content:
                    save_artifact(content, filename, issue_num, state_dir=state_dir)

            # Manager outputs (from manager.{node_id}.*)
            for key, value in result.context.items():
                if key.startswith("manager.") and key.endswith(".final_status"):
                    node_id = key.split(".")[1]
                    iters = result.context.get(f"manager.{node_id}.iterations", [])
                    if iters:
                        import json  # noqa: PLC0415

                        save_artifact(
                            json.dumps(iters, indent=2),
                            f"manager-{node_id}-iterations.json",
                            issue_num,
                            state_dir=state_dir,
                        )
        except Exception:  # noqa: BLE001
            logger.warning("Failed to save pipeline artifacts", exc_info=True)

    # ── Dark Forge ───────────────────────────────────────────────

    async def run_forge(
        self,
        issue: dict[str, Any],
        workspace: str,
        *,
        strategy: str | None = None,
        skip_arch_review: bool = False,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run Dark Forge with issue JSON, workspace, and strategy.

        Args:
            issue: Issue data (number, title, body, labels, etc.).
            workspace: Path to the workspace directory.
            strategy: Deploy strategy override (``"web"`` or ``"console"``).
                Defaults to ``EngineConfig.deploy_strategy``.
            skip_arch_review: If True, skip the arch review + verdict nodes
                and jump straight to spec generation.
            context: Extra context variables.

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        ctx: dict[str, Any] = {
            "issue": issue,
            "workspace": workspace,
            **(context or {}),
        }
        # Strategy precedence: explicit param > workspace config > engine default
        if not strategy and workspace:
            ws_data = self._load_workspace_config(workspace)
            strategy = ws_data.get("strategy", "")
        ctx["strategy"] = strategy or self._engine_cfg.deploy_strategy

        # Skip arch review nodes when configured
        if skip_arch_review:
            ctx["_skip_nodes"] = ["arch_review", "arch_verdict"]

        # Derive issue number and expose for shell node variable expansion
        issue_num = issue.get("number", 0) if isinstance(issue, dict) else 0
        if issue_num:
            ctx["issue_number"] = str(issue_num)

        # Create workflow log for agent visibility
        if workspace and issue_num:
            from dark_factory.engine.workflow_log import WorkflowLog  # noqa: PLC0415

            wf_log = WorkflowLog(
                Path(workspace) / ".dark-factory" / "logs" / f"workflow-{issue_num}.log",
                issue_number=issue_num,
            )
            ctx["_workflow_log"] = str(wf_log.path)

        result = await self.run_pipeline("dark_forge", ctx)

        # Persist key outputs to disk
        self._save_pipeline_artifacts(result, workspace, issue_num)

        return result

    # ── Crucible ─────────────────────────────────────────────────

    async def run_crucible(
        self,
        workspace: str,
        base_sha: str,
        head_sha: str,
        *,
        issue_number: int = 0,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run Crucible with the given SHAs.

        Args:
            workspace: Path to the workspace directory.
            base_sha: Base commit SHA for diff comparison.
            head_sha: Head commit SHA for diff comparison.
            issue_number: Optional issue number for log naming.
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

        if issue_number:
            ctx["issue_number"] = str(issue_number)

        # Inject test_repo from workspace config if not already provided.
        if "test_repo" not in ctx:
            ctx["test_repo"] = self._resolve_crucible_repo(workspace)

        # Create workflow log for agent visibility
        if workspace:
            from dark_factory.engine.workflow_log import WorkflowLog  # noqa: PLC0415

            log_name = f"crucible-{issue_number}" if issue_number else "crucible"
            wf_log = WorkflowLog(
                Path(workspace) / ".dark-factory" / "logs" / f"workflow-{log_name}.log",
                issue_number=issue_number,
            )
            ctx["_workflow_log"] = str(wf_log.path)

        return await self.run_pipeline("crucible", ctx)

    # ── Ouroboros ─────────────────────────────────────────────────

    async def run_ouroboros(
        self,
        trigger: str,
        *,
        workspace: str = "",
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Run Ouroboros with the given trigger type.

        Args:
            trigger: Trigger type (``"scheduled"``, ``"post_cycle"``,
                ``"manual"``, ``"auto_update"``, ``"self_forge"``).
            workspace: Path to the factory workspace directory.
            context: Extra context variables.

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        ctx: dict[str, Any] = {
            "trigger": trigger,
            **(context or {}),
        }

        if workspace:
            ctx["workspace"] = workspace

        # Create workflow log for agent visibility
        if workspace:
            from dark_factory.engine.workflow_log import WorkflowLog  # noqa: PLC0415

            wf_log = WorkflowLog(
                Path(workspace) / ".dark-factory" / "logs" / "workflow-ouroboros.log",
            )
            ctx["_workflow_log"] = str(wf_log.path)

        return await self.run_pipeline("ouroboros", ctx)
