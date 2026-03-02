"""Codergen handler -- LLM-powered node execution.

The codergen handler is the primary integration point between the
Attractor pipeline and the Coding Agent Loop (or any other LLM backend).
It delegates to a CodergenBackend interface, which is deliberately
narrow by design (see our Issue 5 resolution).

Spec reference: attractor-spec §4.5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from dark_factory.engine.agent.abort import AbortSignal
from dark_factory.engine.graph import Graph, Node
from dark_factory.engine.runner import HandlerResult, Outcome
from dark_factory.engine.variable_expansion import expand_node_prompt


class CodergenBackend(Protocol):
    """Backend interface for LLM-powered nodes. Spec §4.5.

    This is intentionally narrow -- the Node carries all configuration
    (llm_model, llm_provider, reasoning_effort, etc.) and the Context
    carries all runtime state. The backend can extract whatever it needs.

    Implementations:
    - Wrap a Coding Agent Session (primary use case)
    - Call the LLM SDK directly (for simple prompts)
    - Shell out to a CLI agent (Claude Code, Codex, etc.)

    Returns either a plain string (auto-wrapped as SUCCESS) or a
    full HandlerResult for richer control over routing and context.
    """

    async def run(
        self,
        node: Node,
        prompt: str,
        context: dict[str, Any],
        abort_signal: AbortSignal | None = None,
    ) -> str | HandlerResult: ...


class CodergenHandler:
    """Handler for codergen/LLM nodes (shape=box, hexagon). Spec §4.5.

    Expands the node's prompt template with context variables,
    then delegates to the CodergenBackend for LLM execution.
    """

    def __init__(self, backend: CodergenBackend | None = None) -> None:
        self._backend = backend

    async def execute(
        self,
        node: Node,
        context: dict[str, Any],
        graph: Graph,
        logs_root: Path | None,
        abort_signal: AbortSignal | None = None,
    ) -> HandlerResult:
        # Build prompt from node's prompt attribute + goal
        prompt = self._expand_prompt(node, context, graph)

        if not prompt:
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=(f"Codergen node '{node.id}' has no prompt"),
            )

        if self._backend is None:
            # No backend configured -- return placeholder
            return HandlerResult(
                status=Outcome.SUCCESS,
                output=f"[No backend configured] Prompt: {prompt[:200]}",
                notes="Codergen executed without backend (dry run)",
            )

        # Delegate to backend
        try:
            result = await self._backend.run(
                node,
                prompt,
                context,
                abort_signal,
            )
        except Exception as exc:  # noqa: BLE001
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )

        # Normalize result to HandlerResult.
        # Truncate outputs stored in context to reduce deep-copy overhead
        # at parallel forks and manager iterations. Full output is still
        # available in HandlerResult.output for artifact writing.
        max_ctx_output = int(node.attrs.get("max_context_output", "2000"))
        if isinstance(result, str):
            # Plain string -> wrap as SUCCESS
            context[f"codergen.{node.id}.output"] = result[:max_ctx_output]
            handler_result = HandlerResult(
                status=Outcome.SUCCESS,
                output=result,
                notes=f"Codergen node '{node.id}' completed",
            )
        else:
            # Already a HandlerResult
            if result.output:
                context[f"codergen.{node.id}.output"] = result.output[:max_ctx_output]
            handler_result = result

        # Store truncated prompt for engine-level artifact writing (Spec §5.6).
        context[f"_artifact_prompt.{node.id}"] = prompt[:1000]

        return handler_result

    def _expand_prompt(self, node: Node, context: dict[str, Any], graph: Graph) -> str:
        """Expand template variables in the node's prompt.

        Uses the variable_expansion module for proper $var and ${var}
        expansion with escaped \\$ support. The graph goal is injected
        into the context for expansion.
        """
        prompt = node.prompt or node.label or ""

        # Merge goal into expansion context
        expand_ctx = dict(context)
        expand_ctx["goal"] = graph.goal

        expanded = expand_node_prompt(prompt, expand_ctx)

        # Inject workflow log instructions (parity with bash agent-protocol.sh)
        wf_log = context.get("_workflow_log", "")
        if wf_log:
            expanded += (
                "\n\n## Workflow Log\n\n"
                "IMPORTANT: Log your progress to this file AS YOU WORK (not at the end):\n"
                f"**{wf_log}**\n\n"
                "Append one line per action using this format:\n"
                "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] STAGE_NAME | ACTION | detail\n\n"
                "Actions: START (your plan), READ (file path), ANALYZE (finding), "
                "DECISION (choice made), ISSUE (problem), DONE (summary).\n\n"
                "CRITICAL: Your FINAL output must be your stage review/deliverable "
                "per your Output Format.\n"
                "NEVER end your session with a log write -- always end by outputting your review.\n"
            )

        return expanded
