"""Codergen handler -- LLM-powered node execution.

The codergen handler is the primary integration point between the
Attractor pipeline and the Coding Agent Loop (or any other LLM backend).
It delegates to a CodergenBackend interface, which is deliberately
narrow by design (see our Issue 5 resolution).

Spec reference: attractor-spec §4.5.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from dark_factory.engine.agent.abort import AbortSignal
from dark_factory.engine.graph import Graph, Node
from dark_factory.engine.runner import HandlerResult, Outcome
from dark_factory.engine.variable_expansion import expand_node_prompt

logger = logging.getLogger(__name__)

# Verdict keywords that agents can emit on their final line to drive
# conditional edge routing (e.g. arch_verdict diamond).
_VERDICT_KEYWORDS: frozenset[str] = frozenset({
    # Dark Forge arch review
    "APPROVED", "NEEDS_CHANGES", "NEEDS_HUMAN",
    # Feature TDD fix loop
    "ALL_PASS", "FIXES_NEEDED",
    # Dark Forge diff validation + retry
    "RELEVANT", "IRRELEVANT", "RETRY",
    # Crucible
    "CLEAN", "BLOCK", "GO", "NO_GO", "NEEDS_LIVE",
    # Ouroboros
    "ALLOW", "DENY", "PASS", "FAIL",
    "HEALTHY", "UNHEALTHY",
    "UPDATE_AVAILABLE", "UP_TO_DATE",
    # Obelisk
    "FACTORY_BUG", "USER_CODE", "INFRASTRUCTURE",
    "FIXED", "ESCALATED",
    "CONTEXT_GATHERED", "FIX_PROPOSED",
})


def _extract_verdict(output: str) -> str:
    """Scan the last non-empty line of *output* for a known verdict keyword.

    Returns the keyword (uppercase) if found, otherwise empty string.
    """
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        # Check if the line IS a verdict or ends with one
        upper = stripped.upper()
        if upper in _VERDICT_KEYWORDS:
            return upper
        # Also check last word (agent might write "Verdict: APPROVED")
        last_word = upper.rsplit(None, 1)[-1] if upper else ""
        if last_word in _VERDICT_KEYWORDS:
            return last_word
        # Only check the last non-empty line
        logger.debug(
            "_extract_verdict: last non-empty line %r (upper=%r) matched no keyword",
            stripped[:120], upper[:120],
        )
        break
    if not output.strip():
        logger.debug("_extract_verdict: output was empty/whitespace-only")
    return ""


# Node ID -> filename for disk artifacts.  Nodes not listed here
# still get written as {node_id}.md.
_ARTIFACT_FILENAMES: dict[str, str] = {
    "gen_design": "design.md",
    "gen_prd": "prd.json",
    "gen_api_contract": "api-contract.yaml",
    "gen_schema": "schema.sql",
    "gen_interfaces": "interfaces.txt",
    "gen_test_strategy": "test-strategy.md",
    "arch_review": "engineering-brief.md",
}


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
            # Extract verdict from final line for conditional edge routing
            verdict = _extract_verdict(result)
            handler_result = HandlerResult(
                status=Outcome.SUCCESS,
                output=result,
                preferred_label=verdict,
                notes=f"Codergen node '{node.id}' completed",
            )
            if verdict:
                context["verdict"] = verdict
                logger.info("Extracted verdict '%s' from node '%s'", verdict, node.id)
        else:
            # Already a HandlerResult
            if result.output:
                context[f"codergen.{node.id}.output"] = result.output[:max_ctx_output]
            handler_result = result

        # Write output to disk and store path reference (arch flow pattern)
        self._write_stage_artifact(node, context, handler_result)

        # Store truncated prompt for engine-level artifact writing (Spec §5.6).
        context[f"_artifact_prompt.{node.id}"] = prompt[:1000]

        return handler_result

    @staticmethod
    def _write_stage_artifact(
        node: Node, context: dict[str, Any], result: HandlerResult,
    ) -> None:
        """Write stage output to disk and store file path in context.

        Follows the bash architecture flow pattern: each stage writes its
        output to .dark-factory/specs/{issue}/ and downstream stages get
        a file path reference via $gen_design_path, $gen_prd_path, etc.
        """
        output = result.output
        if not output:
            return

        workspace = context.get("workspace", "")
        if not workspace:
            return

        issue = context.get("issue", {})
        issue_num = issue.get("number", 0) if isinstance(issue, dict) else 0
        if not issue_num:
            return

        filename = _ARTIFACT_FILENAMES.get(node.id, f"{node.id}.md")
        spec_dir = Path(workspace) / ".dark-factory" / "specs" / str(issue_num)

        try:
            spec_dir.mkdir(parents=True, exist_ok=True)

            # Handle ---SPLIT--- marker: gen_prd outputs test-spec + feature-spec
            if "---SPLIT---" in output:
                parts = output.split("---SPLIT---", 1)
                test_spec = parts[0].strip()
                feature_spec = parts[1].strip() if len(parts) > 1 else ""

                test_path = spec_dir / "test-spec.md"
                feature_path = spec_dir / "feature-spec.md"
                test_path.write_text(test_spec, encoding="utf-8")
                feature_path.write_text(feature_spec, encoding="utf-8")

                context[f"{node.id}_test_path"] = str(test_path)
                context[f"{node.id}_feature_path"] = str(feature_path)
                # Also store issue_number for shell node variable expansion
                context["issue_number"] = str(issue_num)
                logger.info("Split %s into test-spec.md (%d bytes) + feature-spec.md (%d bytes)",
                            node.id, len(test_spec), len(feature_spec))
            else:
                artifact_path = spec_dir / filename
                artifact_path.write_text(output, encoding="utf-8")
                logger.info("Wrote %s (%d bytes)", artifact_path, len(output))

            # Store path reference so downstream prompts can use $gen_design_path etc.
            context[f"{node.id}_path"] = str(spec_dir / filename)
        except OSError:
            logger.warning("Failed to write artifact for node %s", node.id, exc_info=True)

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

        # Inject per-node progress log + shared workflow log instructions.
        workspace = context.get("workspace", "")
        if workspace:
            node_log = str(Path(workspace) / ".dark-factory" / "logs" / f"{node.id}.log")
            wf_log = context.get("_workflow_log", "")

            log_section = (
                "\n\n## Progress Log\n\n"
                f"Log progress to: **{node_log}**\n"
            )
            if wf_log:
                log_section += (
                    f"Also log key milestones to: **{wf_log}**\n"
                )
            log_section += (
                "Log at major milestones (START, DONE, and key decisions). "
                "Your FINAL output must be your deliverable, not a log entry.\n"
            )
            expanded += log_section

        return expanded
