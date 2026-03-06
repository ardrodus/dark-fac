"""ClaudeCodeBackend -- shells out to Claude Code CLI for LLM nodes.

A thin CodergenBackend that delegates to ``claude --print`` via
``asyncio.create_subprocess_exec`` instead of calling LLM APIs directly.
No httpx, boto3, or provider management -- just stdin/stdout piping.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from typing import Any

from dark_factory.engine.agent.abort import AbortSignal
from dark_factory.engine.graph import Node
from dark_factory.engine.runner import HandlerResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaudeCodeConfig:
    """Configuration for the Claude Code CLI backend."""

    claude_path: str = "claude"
    model: str = ""


class ClaudeCodeBackend:
    """CodergenBackend that shells out to ``claude --print``.

    Implements the ``run()`` protocol so it can be passed directly to
    :class:`CodergenHandler`.

    Usage::

        backend = ClaudeCodeBackend(ClaudeCodeConfig(model="claude-sonnet-4-5"))
        handler = CodergenHandler(backend=backend)
    """

    def __init__(
        self,
        config: ClaudeCodeConfig | None = None,
        resource_limiter: Any | None = None,
    ) -> None:
        self._config = config or ClaudeCodeConfig()
        self._limiter = resource_limiter

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
        # Resolve the claude executable path.  On Windows,
        # asyncio.create_subprocess_exec cannot find .cmd/.bat wrappers
        # (e.g. claude.cmd) so we use shutil.which() to get the full path.
        claude_exe = shutil.which(self._config.claude_path)
        if claude_exe is None:
            msg = (
                f"Claude CLI not found: '{self._config.claude_path}' is not on PATH. "
                "Install it or set claude_path in ClaudeCodeConfig."
            )
            raise FileNotFoundError(msg)

        cmd: list[str] = [claude_exe, "--print", "--dangerously-skip-permissions"]

        # Model: node override > backend config
        model = node.llm_model or self._config.model
        if model:
            cmd.extend(["--model", model])

        # System prompt from node attrs
        system_prompt: str = node.attrs.get("system_prompt", "")
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        # Resolve workspace for subprocess cwd so the agent operates
        # in the target workspace, not the dark_factory source repo.
        import os  # noqa: PLC0415

        workspace = context.get("workspace", "")
        cwd = workspace if workspace and os.path.isdir(workspace) else None

        logger.info(
            "[claude-backend] node=%s cmd=%s prompt_len=%d cwd=%s",
            node.id, " ".join(cmd), len(prompt), cwd or "(inherit)",
        )

        sem = self._limiter.get_async_semaphore() if self._limiter else None
        if sem is not None:
            await sem.acquire()
        try:
            # Strip CLAUDECODE env var so nested claude --print doesn't refuse to run
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )

            stdout_bytes, stderr_bytes = await proc.communicate(
                input=prompt.encode("utf-8"),
            )
        finally:
            if sem is not None:
                sem.release()

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        logger.info(
            "[claude-backend] node=%s exit=%d stdout=%d bytes stderr=%d bytes",
            node.id, proc.returncode, len(stdout_bytes), len(stderr_bytes),
        )

        if proc.returncode != 0:
            # Log full details on failure for debugging
            logger.error(
                "[claude-backend] FAILED node=%s exit=%d\n"
                "  cmd: %s\n"
                "  prompt (first 500 chars): %s\n"
                "  stdout (first 1000 chars): %s\n"
                "  stderr: %s",
                node.id,
                proc.returncode,
                " ".join(cmd),
                prompt[:500],
                stdout_text[:1000],
                stderr_text or "(empty)",
            )
            msg = (
                f"claude --print exited with code {proc.returncode}: "
                f"{stderr_text or '(no stderr)'}"
            )
            raise RuntimeError(msg)

        if not stdout_text.strip():
            logger.warning(
                "[claude-backend] node=%s returned empty stdout (exit=0)",
                node.id,
            )

        return stdout_text
