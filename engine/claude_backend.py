"""ClaudeCodeBackend -- shells out to Claude Code CLI for LLM nodes.

A thin CodergenBackend that delegates to ``claude --print`` via
``asyncio.create_subprocess_exec`` instead of calling LLM APIs directly.
No httpx, boto3, or provider management -- just stdin/stdout piping.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from dark_factory.engine.agent.abort import AbortSignal
from dark_factory.engine.graph import Node
from dark_factory.engine.runner import HandlerResult


@dataclass(frozen=True, slots=True)
class ClaudeCodeConfig:
    """Configuration for the Claude Code CLI backend."""

    claude_path: str = "claude"
    model: str = ""


class ClaudeCodeBackend:
    """CodergenBackend that shells out to ``claude --print``.

    Implements the same ``run()`` protocol as :class:`AgentLoopBackend`
    and :class:`DirectLLMBackend` so it can be passed directly to
    :class:`CodergenHandler`.

    Usage::

        backend = ClaudeCodeBackend(ClaudeCodeConfig(model="claude-sonnet-4-5"))
        handler = CodergenHandler(backend=backend)
    """

    def __init__(self, config: ClaudeCodeConfig | None = None) -> None:
        self._config = config or ClaudeCodeConfig()

    async def run(
        self,
        node: Node,
        prompt: str,
        context: dict[str, Any],
        abort_signal: AbortSignal | None = None,
    ) -> str | HandlerResult:
        """Execute a prompt via ``claude --print``.

        Builds a CLI command with optional ``--model`` and
        ``--system-prompt`` flags, pipes the prompt on stdin,
        and returns stdout as a string.

        Raises ``RuntimeError`` on non-zero exit codes.
        """
        cmd: list[str] = [self._config.claude_path, "--print"]

        # Model: node override > backend config
        model = node.llm_model or self._config.model
        if model:
            cmd.extend(["--model", model])

        # System prompt from node attrs
        system_prompt: str = node.attrs.get("system_prompt", "")
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await proc.communicate(
            input=prompt.encode("utf-8"),
        )

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            msg = (
                f"claude --print exited with code {proc.returncode}: "
                f"{stderr_text}"
            )
            raise RuntimeError(msg)

        return stdout_bytes.decode("utf-8", errors="replace")
