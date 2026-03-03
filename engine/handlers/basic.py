"""Basic node handlers: start, exit, conditional, tool.

These are the simple handlers that don't require external backends
(unlike codergen which needs an LLM, or human which needs an interviewer).

Spec reference: attractor-spec §4.3-4.10.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from dark_factory.engine.agent.abort import AbortSignal
from dark_factory.engine.graph import Graph, Node
from dark_factory.engine.runner import HandlerResult, Outcome


class StartHandler:
    """Handler for start nodes (shape=Mdiamond). Spec §4.3.

    The start handler is a no-op -- it simply passes through
    with SUCCESS status. Its purpose is to mark the entry point.
    """

    async def execute(
        self,
        node: Node,
        context: dict[str, Any],
        graph: Graph,
        logs_root: Path | None,
        abort_signal: AbortSignal | None = None,
    ) -> HandlerResult:
        return HandlerResult(
            status=Outcome.SUCCESS,
            notes=f"Pipeline started at node '{node.id}'",
        )


class ExitHandler:
    """Handler for exit nodes (shape=Msquare). Spec §4.4.

    The exit handler is a no-op -- it marks the terminal node.
    Goal gate checking happens in the engine, not the handler.
    """

    async def execute(
        self,
        node: Node,
        context: dict[str, Any],
        graph: Graph,
        logs_root: Path | None,
        abort_signal: AbortSignal | None = None,
    ) -> HandlerResult:
        return HandlerResult(
            status=Outcome.SUCCESS,
            notes=f"Pipeline reached exit node '{node.id}'",
        )


class ConditionalHandler:
    """Handler for conditional/branching nodes (shape=diamond). Spec §4.7.

    The conditional handler propagates the previous node's preferred_label
    so the edge selector can match it against outgoing edge labels.
    This enables patterns like: manager → diamond → [APPROVED|NEEDS_HUMAN].

    If the node has a prompt, it's stored in context for edge conditions
    to reference.
    """

    async def execute(
        self,
        node: Node,
        context: dict[str, Any],
        graph: Graph,
        logs_root: Path | None,
        abort_signal: AbortSignal | None = None,
    ) -> HandlerResult:
        # If the node has a prompt, evaluate it as context
        if node.prompt:
            context[f"conditional.{node.id}"] = node.prompt

        # Propagate previous node's preferred_label for edge routing
        prev_label = context.get("_preferred_label", "")

        return HandlerResult(
            status=Outcome.SUCCESS,
            preferred_label=str(prev_label) if prev_label else "",
            notes=f"Conditional branch at '{node.id}'",
        )


class ToolHandler:
    """Handler for tool/script nodes (shape=parallelogram). Spec §4.10.

    Executes a shell command defined in the node's prompt attribute.
    The command runs in the pipeline's working directory.
    """

    async def execute(
        self,
        node: Node,
        context: dict[str, Any],
        graph: Graph,
        logs_root: Path | None,
        abort_signal: AbortSignal | None = None,
    ) -> HandlerResult:
        command = node.prompt or node.attrs.get("command", "")
        if not command:
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=f"Tool node '{node.id}' has no command",
            )

        # Variable expansion in command (shell-safe quoting to prevent injection)
        for key, value in context.items():
            if isinstance(value, str):
                # Convert Windows backslash paths to forward slashes for bash.
                # Without this, `cd C:\Sandboxes\...` fails in bash because
                # backslashes are interpreted as escape characters.
                if os.name == "nt" and re.match(r"^[A-Za-z]:\\", value):
                    value = value.replace("\\", "/")
                safe_value = shlex.quote(value)
                command = command.replace(f"${{{key}}}", safe_value)
                command = command.replace(f"${key}", safe_value)

        timeout_str = node.timeout or "120s"
        timeout_seconds = _parse_duration(timeout_str)

        try:
            result = await asyncio.to_thread(
                subprocess.run,  # noqa: S603
                ["bash", "-c", command],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=os.getcwd(),
                start_new_session=True,
            )
        except subprocess.TimeoutExpired:
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=(f"Command timed out after {timeout_seconds}s"),
                output=f"Timeout: {command}",
            )
        except OSError as e:
            return HandlerResult(
                status=Outcome.FAIL,
                failure_reason=str(e),
            )

        output = result.stdout or ""
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"

        if result.returncode == 0:
            # Store output in context for downstream nodes
            context[f"tool.{node.id}.output"] = output.strip()
            return HandlerResult(
                status=Outcome.SUCCESS,
                output=output,
                preferred_label="PASS",
                notes="Command succeeded (exit 0)",
            )

        return HandlerResult(
            status=Outcome.FAIL,
            failure_reason=f"Exit code {result.returncode}",
            output=output,
            preferred_label="FAIL",
            notes=f"Command failed (exit {result.returncode})",
        )


def _parse_duration(duration_str: str) -> int:
    """Parse a duration string like '5m', '30s', '2h' to seconds."""
    duration_str = duration_str.strip().lower()

    if duration_str.endswith("h"):
        return int(float(duration_str[:-1]) * 3600)
    if duration_str.endswith("m"):
        return int(float(duration_str[:-1]) * 60)
    if duration_str.endswith("s"):
        return int(float(duration_str[:-1]))

    # Try plain integer (assume seconds)
    try:
        return int(duration_str)
    except ValueError:
        return 120  # default
