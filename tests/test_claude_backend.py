"""Tests for ClaudeCodeBackend -- the CLI-based CodergenBackend."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factory.engine.claude_backend import ClaudeCodeBackend, ClaudeCodeConfig
from factory.engine.graph import Node
from factory.engine.runner import HandlerResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(
    node_id: str = "test_node",
    llm_model: str = "",
    attrs: dict | None = None,
) -> Node:
    return Node(id=node_id, llm_model=llm_model, attrs=attrs or {})


def _fake_process(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClaudeCodeConfig:
    def test_defaults(self) -> None:
        cfg = ClaudeCodeConfig()
        assert cfg.claude_path == "claude"
        assert cfg.model == ""

    def test_custom(self) -> None:
        cfg = ClaudeCodeConfig(claude_path="/usr/local/bin/claude", model="opus")
        assert cfg.claude_path == "/usr/local/bin/claude"
        assert cfg.model == "opus"

    def test_frozen(self) -> None:
        cfg = ClaudeCodeConfig()
        with pytest.raises(AttributeError):
            cfg.model = "nope"  # type: ignore[misc]


class TestClaudeCodeBackendInit:
    def test_default_config(self) -> None:
        backend = ClaudeCodeBackend()
        assert backend._config.claude_path == "claude"

    def test_custom_config(self) -> None:
        cfg = ClaudeCodeConfig(model="sonnet")
        backend = ClaudeCodeBackend(cfg)
        assert backend._config.model == "sonnet"


class TestClaudeCodeBackendRun:
    @pytest.mark.asyncio
    async def test_basic_prompt(self) -> None:
        """Pipes prompt to stdin, returns stdout."""
        proc = _fake_process(stdout=b"Hello world")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            backend = ClaudeCodeBackend()
            result = await backend.run(_node(), "Say hello", {})

        assert result == "Hello world"
        mock_exec.assert_called_once()
        # Verify --print flag
        args = mock_exec.call_args[0]
        assert args == ("claude", "--print")
        # Verify prompt piped via stdin
        proc.communicate.assert_called_once_with(input=b"Say hello")

    @pytest.mark.asyncio
    async def test_model_from_config(self) -> None:
        """--model flag from config when node has no llm_model."""
        proc = _fake_process(stdout=b"ok")
        cfg = ClaudeCodeConfig(model="claude-sonnet-4-5")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            backend = ClaudeCodeBackend(cfg)
            await backend.run(_node(), "test", {})

        args = mock_exec.call_args[0]
        assert "--model" in args
        assert "claude-sonnet-4-5" in args

    @pytest.mark.asyncio
    async def test_model_from_node_overrides_config(self) -> None:
        """Node llm_model takes priority over config model."""
        proc = _fake_process(stdout=b"ok")
        cfg = ClaudeCodeConfig(model="config-model")
        node = _node(llm_model="node-model")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            backend = ClaudeCodeBackend(cfg)
            await backend.run(node, "test", {})

        args = mock_exec.call_args[0]
        assert "node-model" in args
        assert "config-model" not in args

    @pytest.mark.asyncio
    async def test_no_model_flag_when_empty(self) -> None:
        """No --model flag when neither config nor node specify a model."""
        proc = _fake_process(stdout=b"ok")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            backend = ClaudeCodeBackend()
            await backend.run(_node(), "test", {})

        args = mock_exec.call_args[0]
        assert "--model" not in args

    @pytest.mark.asyncio
    async def test_system_prompt_from_node_attrs(self) -> None:
        """--system-prompt flag when node attrs include system_prompt."""
        proc = _fake_process(stdout=b"ok")
        node = _node(attrs={"system_prompt": "You are helpful."})

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            backend = ClaudeCodeBackend()
            await backend.run(node, "test", {})

        args = mock_exec.call_args[0]
        assert "--system-prompt" in args
        assert "You are helpful." in args

    @pytest.mark.asyncio
    async def test_no_system_prompt_when_empty(self) -> None:
        """No --system-prompt flag when node has no system_prompt."""
        proc = _fake_process(stdout=b"ok")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            backend = ClaudeCodeBackend()
            await backend.run(_node(), "test", {})

        args = mock_exec.call_args[0]
        assert "--system-prompt" not in args

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_runtime_error(self) -> None:
        """RuntimeError on non-zero exit code with stderr content."""
        proc = _fake_process(returncode=1, stderr=b"Something went wrong")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc):
            backend = ClaudeCodeBackend()
            with pytest.raises(RuntimeError, match="exited with code 1"):
                await backend.run(_node(), "test", {})

    @pytest.mark.asyncio
    async def test_error_includes_stderr(self) -> None:
        """RuntimeError message includes stderr content."""
        proc = _fake_process(returncode=2, stderr=b"API key invalid")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc):
            backend = ClaudeCodeBackend()
            with pytest.raises(RuntimeError, match="API key invalid"):
                await backend.run(_node(), "test", {})

    @pytest.mark.asyncio
    async def test_custom_claude_path(self) -> None:
        """Uses custom claude_path from config."""
        proc = _fake_process(stdout=b"ok")
        cfg = ClaudeCodeConfig(claude_path="/opt/bin/claude")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            backend = ClaudeCodeBackend(cfg)
            await backend.run(_node(), "test", {})

        args = mock_exec.call_args[0]
        assert args[0] == "/opt/bin/claude"

    @pytest.mark.asyncio
    async def test_all_flags_together(self) -> None:
        """Verify correct arg ordering with all flags set."""
        proc = _fake_process(stdout=b"response")
        cfg = ClaudeCodeConfig(claude_path="claude", model="my-model")
        node = _node(attrs={"system_prompt": "Be brief."})

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            backend = ClaudeCodeBackend(cfg)
            result = await backend.run(node, "do stuff", {})

        args = mock_exec.call_args[0]
        assert args[0] == "claude"
        assert args[1] == "--print"
        assert "--model" in args
        assert "my-model" in args
        assert "--system-prompt" in args
        assert "Be brief." in args
        assert result == "response"

    @pytest.mark.asyncio
    async def test_stdin_encoding(self) -> None:
        """Non-ASCII prompt is encoded as UTF-8."""
        proc = _fake_process(stdout=b"ok")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc):
            backend = ClaudeCodeBackend()
            await backend.run(_node(), "Héllo wörld", {})

        proc.communicate.assert_called_once_with(input="Héllo wörld".encode())

    @pytest.mark.asyncio
    async def test_returns_string_not_handler_result(self) -> None:
        """On success, returns a plain string (CodergenHandler wraps it)."""
        proc = _fake_process(stdout=b"done")

        with patch("factory.engine.claude_backend.asyncio.create_subprocess_exec", return_value=proc):
            backend = ClaudeCodeBackend()
            result = await backend.run(_node(), "test", {})

        assert isinstance(result, str)
        assert not isinstance(result, HandlerResult)
