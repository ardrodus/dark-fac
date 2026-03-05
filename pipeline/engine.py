"""FactoryPipelineEngine — the "Execute" phase.

This is the third and final phase of the Invoke → Gather Dependencies →
Execute pattern.  By the time code reaches this module:

1. The dispatch layer (``cli/dispatch.py``) has resolved the repo and
   validated inputs.  ← **Invoke**
2. ``workspace/manager.py`` has cloned/pulled the repo and bootstrapped
   a self-contained workspace with agents, DOT files, and scripts.
   ← **Gather Dependencies**
3. This engine loads a DOT pipeline from the workspace and walks the
   graph — each node is an LLM agent prompt.  ← **Execute**

ALL workflow logic lives in the DOT files
-----------------------------------------
The engine is a generic graph runner.  It does not know what "Dark Forge"
or "Crucible" are — it just parses nodes, resolves edges, and dispatches
prompts.  Branching, retries, parallelism, and success conditions are
all expressed declaratively in the ``.dot`` files.

If you need to add workflow logic (new steps, conditional branches,
retry loops), modify or create a DOT file — do NOT add it to this
module or to the dispatch layer.

Usage::

    engine = FactoryPipelineEngine()
    result = await engine.run_pipeline("dark_forge", {"workspace_root": ws.path})
    result = await engine.run_pipeline("crucible", {"workspace_root": ws.path, "pr_number": "42"})
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.engine.events import PipelineEvent

logger = logging.getLogger(__name__)



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
        on_human_gate: Callable[[str, str], str] | None = None,
    ) -> None:
        from dark_factory.engine.claude_backend import (  # noqa: PLC0415
            ClaudeCodeBackend,
            ClaudeCodeConfig,
        )
        from dark_factory.engine.config import load_engine_config  # noqa: PLC0415
        from dark_factory.engine.handlers import register_default_handlers  # noqa: PLC0415
        from dark_factory.engine.resource_limiter import ResourceLimiter  # noqa: PLC0415
        from dark_factory.engine.runner import HandlerRegistry  # noqa: PLC0415

        # --- Gather deps (once, at construction time) ---
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

        # Handler registry — built once, reused across all run_pipeline() calls
        self._registry = HandlerRegistry()
        register_default_handlers(self._registry, codergen_backend=self._backend)

        if on_human_gate:
            from dark_factory.engine.handlers import (  # noqa: PLC0415
                CallbackInterviewer,
                HumanHandler,
            )

            _gate_cb = on_human_gate

            async def _human_callback(
                text: str, options: list[str] | None, stage: str | None,
            ) -> str:
                return _gate_cb(stage or "", text)

            interviewer = CallbackInterviewer(callback=_human_callback)
            self._registry.register("wait.human", HumanHandler(interviewer=interviewer))

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

        Follows **prepare → gather deps → run**:

        1. **Prepare**: resolve workspace, discover pipelines, load agents,
           inject strategy.
        2. **Gather deps**: engine config, backend, handler registry (in
           ``__init__``); logs dir (found from workspace).
        3. **Run**: parse DOT → validate → apply stylesheet →
           ``runner.run_pipeline()``.

        When ``context["workspace_root"]`` is set, the workspace is used
        as the primary source for pipelines (``{root}/pipeline/``) and
        agent personas (``{root}/agents/``).  The context variable
        ``$workspace`` is set to ``{root}/repo/`` so DOT prompts can
        reference the code directory.

        Args:
            name: Pipeline stem name (e.g. ``"dark_forge"``, ``"sentinel"``).
            context: Variables available to pipeline nodes via ``$variable``.
                Set ``workspace_root`` to the workspace directory to enable
                workspace-scoped pipeline and agent resolution.
            on_event: Per-call event callback (overrides constructor default).

        Returns:
            :class:`~factory.engine.runner.PipelineResult`.
        """
        from dark_factory.engine.parser import parse_dot  # noqa: PLC0415
        from dark_factory.engine.runner import run_pipeline as _run_pipeline  # noqa: PLC0415
        from dark_factory.engine.stylesheet import apply_stylesheet  # noqa: PLC0415
        from dark_factory.engine.validation import validate_or_raise  # noqa: PLC0415
        from dark_factory.pipeline.loader import discover_pipelines  # noqa: PLC0415

        ctx = dict(context or {})

        # --- PREPARE ---

        # Resolve workspace root — this is the self-contained workspace
        # directory containing repo/, agents/, pipeline/, scripts/, logs/.
        ws_root_str = ctx.pop("workspace_root", "")
        ws_root = Path(ws_root_str) if ws_root_str else None

        # Set $workspace to the repo subdirectory so DOT prompts
        # reference the code directory (backward compatible).
        if ws_root and not ctx.get("workspace"):
            ctx["workspace"] = str(ws_root / "repo")

        # Discover pipelines: workspace pipeline/ dir takes priority.
        if ws_root:
            pipelines = discover_pipelines(
                project_root=self._config_start,
                workspace_pipeline_dir=ws_root / "pipeline",
            )
        else:
            pipelines = discover_pipelines(project_root=self._config_start)

        dotfile = pipelines.get(name)
        if dotfile is None:
            msg = (
                f"Pipeline '{name}' not found. "
                f"Available: {sorted(pipelines)}"
            )
            raise FileNotFoundError(msg)

        # Inject strategy so arch_review_${strategy}.dot resolves correctly.
        ctx.setdefault("strategy", self._engine_cfg.deploy_strategy)

        # Load agent persona files into context so DOT prompts can
        # reference them as $sa_security_console_agent etc.
        self._load_agent_personas(ctx, workspace_root=ws_root)

        # Store workspace_root in context for child_graph resolution
        if ws_root:
            ctx["_workspace_root"] = str(ws_root)

        # --- GATHER DEPS (logs dir — found from workspace) ---

        logs_dir = self._derive_logs_dir(name, ctx, workspace_root=ws_root)
        logs_root = None
        if logs_dir:
            logs_root = Path(logs_dir)
            logs_root.mkdir(parents=True, exist_ok=True)

        # --- RUN: parse → validate → stylesheet → execute ---

        path = Path(dotfile)
        if not path.exists():
            raise FileNotFoundError(f"Pipeline file not found: {dotfile}")

        source = path.read_text(encoding="utf-8")
        graph = parse_dot(source)
        validate_or_raise(graph)

        # Apply model stylesheet from engine config
        if self._engine_cfg.model_stylesheet and not graph.model_stylesheet:
            graph.model_stylesheet = self._engine_cfg.model_stylesheet
            apply_stylesheet(graph)

        event_cb = on_event or self._on_event
        return await _run_pipeline(
            graph,
            self._registry,
            context=ctx,
            logs_root=logs_root,
            on_event=event_cb,
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
    def _load_agent_personas(
        ctx: dict[str, Any],
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Load agent .md files into context for $variable expansion in prompts.

        Resolution order:
        1. Workspace agents dir (``{workspace_root}/agents/``) — highest priority.
        2. Source repo agents dir (fallback for non-workspace runs).

        Each file ``sa-security-console.md`` becomes context key
        ``sa_security_console_agent`` (hyphens to underscores, stem + _agent).
        """
        # Try workspace agents first, then fall back to source repo.
        agents_dirs: list[Path] = []
        if workspace_root:
            agents_dirs.append(workspace_root / "agents")
        agents_dirs.append(Path(__file__).resolve().parent.parent / "agents")

        for agents_dir in agents_dirs:
            if not agents_dir.is_dir():
                continue
            for md_file in agents_dir.glob("*.md"):
                key = md_file.stem.replace("-", "_") + "_agent"
                if key not in ctx:
                    try:
                        ctx[key] = md_file.read_text(encoding="utf-8")
                    except OSError:
                        pass

    @staticmethod
    def _derive_logs_dir(
        name: str,
        ctx: dict[str, Any],
        *,
        workspace_root: Path | None = None,
    ) -> str | None:
        """Derive a logs directory from workspace + issue context.

        When *workspace_root* is set, logs go to ``{root}/logs/``.
        Falls back to ``{workspace}/.dark-factory/logs/`` for legacy callers.
        """
        issue = ctx.get("issue", {})
        issue_num = issue.get("number", 0) if isinstance(issue, dict) else 0
        suffix = f"pipeline-{name}-{issue_num}" if issue_num else f"pipeline-{name}"

        if workspace_root:
            return str(workspace_root / "logs" / suffix)

        workspace = ctx.get("workspace", "")
        if workspace:
            return str(Path(workspace) / ".dark-factory" / "logs" / suffix)
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

