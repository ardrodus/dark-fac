"""Tests for workspace-scoped tool execution and per-node security policies.

Covers US-202 acceptance criteria:
- Tool execution uses workspace-scoped paths (not global filesystem)
- Test Writer tools confined to tests/ directory only
- Feature Writer tools confined to src/ and lib/ directories only
- Code Reviewer tools are read-only (no write/edit/shell)
- Security policies from factory/engine/agent/registry.py enforced per-node
- Workspace path injected into tool execution context
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from factory.engine.agent.registry import (
    _READ_TOOLS,
    _WRITE_TOOLS,
    ROLE_POLICIES,
    SecurityPolicy,
    ToolRegistry,
    check_security_policy,
    resolve_security_policy,
)
from factory.engine.types import ContentPart, ContentPartKind

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _make_tool_call(
    name: str, arguments: dict[str, Any] | None = None, call_id: str = "call-1"
) -> ContentPart:
    """Create a ContentPart with TOOL_CALL kind."""
    return ContentPart(
        kind=ContentPartKind.TOOL_CALL,
        name=name,
        tool_call_id=call_id,
        arguments=arguments or {},
    )


def _make_registry(
    policy: SecurityPolicy | None = None,
    workspace_root: Path | None = None,
) -> ToolRegistry:
    """Create a ToolRegistry with optional security policy."""
    registry = ToolRegistry(
        security_policy=policy,
        workspace_root=workspace_root,
    )

    # Register a dummy tool for each known tool name
    for tool_name in (
        "read_file", "read_many_files", "grep", "glob", "list_dir",
        "write_file", "edit_file", "apply_patch", "shell",
    ):
        tool = MagicMock()
        tool.name = tool_name
        tool.parameters = {"type": "object", "properties": {}}

        async def _handler(**kwargs: Any) -> str:  # noqa: ARG001
            return "ok"

        tool.execute = _handler
        registry.register(tool)

    return registry


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ------------------------------------------------------------------ #
# SecurityPolicy dataclass tests
# ------------------------------------------------------------------ #


class TestSecurityPolicy:
    def test_default_allows_all_tools(self) -> None:
        policy = SecurityPolicy()
        assert "read_file" in policy.allowed_tools
        assert "write_file" in policy.allowed_tools
        assert "shell" in policy.allowed_tools

    def test_frozen(self) -> None:
        policy = SecurityPolicy()
        with pytest.raises(AttributeError):
            policy.read_only = True  # type: ignore[misc]


# ------------------------------------------------------------------ #
# ROLE_POLICIES tests
# ------------------------------------------------------------------ #


class TestRolePolicies:
    def test_test_writer_has_write_tools(self) -> None:
        p = ROLE_POLICIES["test_writer"]
        assert "write_file" in p.allowed_tools
        assert "edit_file" in p.allowed_tools
        assert "shell" in p.allowed_tools

    def test_test_writer_confined_to_tests(self) -> None:
        p = ROLE_POLICIES["test_writer"]
        assert p.writable_paths == ("tests/",)

    def test_feature_writer_confined_to_src_lib(self) -> None:
        p = ROLE_POLICIES["feature_writer"]
        assert p.writable_paths == ("src/", "lib/")

    def test_code_reviewer_read_only(self) -> None:
        p = ROLE_POLICIES["code_reviewer"]
        assert p.read_only is True
        assert "write_file" not in p.allowed_tools
        assert "edit_file" not in p.allowed_tools
        assert "shell" not in p.allowed_tools
        assert "apply_patch" not in p.allowed_tools

    def test_code_reviewer_has_read_tools(self) -> None:
        p = ROLE_POLICIES["code_reviewer"]
        for tool in _READ_TOOLS:
            assert tool in p.allowed_tools


# ------------------------------------------------------------------ #
# resolve_security_policy tests
# ------------------------------------------------------------------ #


class TestResolveSecurityPolicy:
    def test_explicit_role_attribute(self) -> None:
        policy = resolve_security_policy("some_node", {"role": "test_writer"})
        assert policy is not None
        assert policy.writable_paths == ("tests/",)

    def test_node_id_matching(self) -> None:
        policy = resolve_security_policy("feature_writer_1")
        assert policy is not None
        assert policy.writable_paths == ("src/", "lib/")

    def test_node_id_matching_with_hyphens(self) -> None:
        policy = resolve_security_policy("code-reviewer-main")
        assert policy is not None
        assert policy.read_only is True

    def test_unknown_node_returns_none(self) -> None:
        policy = resolve_security_policy("unknown_node")
        assert policy is None

    def test_role_attr_takes_precedence(self) -> None:
        # Node ID matches feature_writer, but role attr says code_reviewer
        policy = resolve_security_policy(
            "feature_writer_1", {"role": "code_reviewer"}
        )
        assert policy is not None
        assert policy.read_only is True


# ------------------------------------------------------------------ #
# check_security_policy tests
# ------------------------------------------------------------------ #


class TestCheckSecurityPolicy:
    def test_allowed_tool_passes(self) -> None:
        policy = ROLE_POLICIES["test_writer"]
        result = check_security_policy("read_file", {"path": "foo.py"}, policy, None)
        assert result is None

    def test_disallowed_tool_blocked(self) -> None:
        policy = ROLE_POLICIES["code_reviewer"]
        result = check_security_policy("write_file", {"path": "foo.py"}, policy, None)
        assert result is not None
        assert "Security policy violation" in result
        assert "write_file" in result

    def test_shell_blocked_for_code_reviewer(self) -> None:
        policy = ROLE_POLICIES["code_reviewer"]
        result = check_security_policy("shell", {"command": "ls"}, policy, None)
        assert result is not None
        assert "shell" in result

    def test_write_in_allowed_dir_passes(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["test_writer"]
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_foo.py"
        test_file.touch()

        result = check_security_policy(
            "write_file", {"path": str(test_file)}, policy, tmp_path,
        )
        assert result is None

    def test_write_outside_allowed_dir_blocked(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["test_writer"]
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src_file = src_dir / "main.py"
        src_file.touch()

        result = check_security_policy(
            "write_file", {"path": str(src_file)}, policy, tmp_path,
        )
        assert result is not None
        assert "Security policy violation" in result
        assert "outside allowed directories" in result

    def test_feature_writer_can_write_to_src(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["feature_writer"]
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src_file = src_dir / "module.py"
        src_file.touch()

        result = check_security_policy(
            "write_file", {"path": str(src_file)}, policy, tmp_path,
        )
        assert result is None

    def test_feature_writer_can_write_to_lib(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["feature_writer"]
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        lib_file = lib_dir / "helper.py"
        lib_file.touch()

        result = check_security_policy(
            "write_file", {"path": str(lib_file)}, policy, tmp_path,
        )
        assert result is None

    def test_feature_writer_blocked_from_tests(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["feature_writer"]
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        test_file = test_dir / "test_main.py"
        test_file.touch()

        result = check_security_policy(
            "write_file", {"path": str(test_file)}, policy, tmp_path,
        )
        assert result is not None
        assert "outside allowed directories" in result

    def test_path_outside_workspace_blocked(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["test_writer"]
        result = check_security_policy(
            "write_file", {"path": "/etc/passwd"}, policy, tmp_path,
        )
        assert result is not None
        assert "outside workspace" in result

    def test_edit_file_confined(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["test_writer"]
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src_file = src_dir / "main.py"
        src_file.touch()

        result = check_security_policy(
            "edit_file",
            {"path": str(src_file), "old_string": "x", "new_string": "y"},
            policy,
            tmp_path,
        )
        assert result is not None
        assert "outside allowed directories" in result

    def test_no_writable_paths_allows_all(self, tmp_path: Path) -> None:
        # Policy with no writable_paths restriction
        policy = SecurityPolicy(allowed_tools=_WRITE_TOOLS)
        src_dir = tmp_path / "anywhere"
        src_dir.mkdir()
        src_file = src_dir / "file.py"
        src_file.touch()

        result = check_security_policy(
            "write_file", {"path": str(src_file)}, policy, tmp_path,
        )
        assert result is None

    def test_no_workspace_root_skips_path_check(self) -> None:
        policy = ROLE_POLICIES["test_writer"]
        result = check_security_policy(
            "write_file", {"path": "/some/path/tests/foo.py"}, policy, None,
        )
        # Without workspace_root, path confinement is not enforced
        assert result is None

    def test_read_tools_pass_regardless_of_writable_paths(
        self, tmp_path: Path,
    ) -> None:
        policy = ROLE_POLICIES["test_writer"]
        src_file = tmp_path / "src" / "main.py"
        # read_file is not in _WRITE_TOOLS, so writable_paths doesn't apply
        result = check_security_policy(
            "read_file", {"path": str(src_file)}, policy, tmp_path,
        )
        assert result is None

    def test_apply_patch_uses_working_dir(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["test_writer"]
        # apply_patch uses working_dir, not path
        result = check_security_policy(
            "apply_patch",
            {"patch": "...", "working_dir": str(tmp_path / "src")},
            policy,
            tmp_path,
        )
        assert result is not None
        assert "outside allowed directories" in result


# ------------------------------------------------------------------ #
# ToolRegistry security enforcement tests
# ------------------------------------------------------------------ #


class TestToolRegistrySecurity:
    """Test security enforcement at the ToolRegistry level."""

    def _execute(
        self, registry: ToolRegistry, tc: ContentPart,
    ) -> ContentPart:
        """Execute a tool call and return the result."""
        return _run(registry.execute_tool_call(tc))

    def test_no_policy_allows_everything(self) -> None:
        registry = _make_registry(policy=None)
        tc = _make_tool_call("write_file", {"path": "/anywhere/file.py"})
        result = self._execute(registry, tc)
        assert not result.is_error

    def test_code_reviewer_blocks_write(self) -> None:
        policy = ROLE_POLICIES["code_reviewer"]
        registry = _make_registry(policy=policy)
        tc = _make_tool_call("write_file", {"path": "foo.py"})
        result = self._execute(registry, tc)
        assert result.is_error
        assert "Security policy violation" in result.output

    def test_code_reviewer_allows_read(self) -> None:
        policy = ROLE_POLICIES["code_reviewer"]
        registry = _make_registry(policy=policy)
        tc = _make_tool_call("read_file", {"path": "foo.py"})
        result = self._execute(registry, tc)
        assert not result.is_error

    def test_code_reviewer_blocks_shell(self) -> None:
        policy = ROLE_POLICIES["code_reviewer"]
        registry = _make_registry(policy=policy)
        tc = _make_tool_call("shell", {"command": "ls"})
        result = self._execute(registry, tc)
        assert result.is_error
        assert "Security policy violation" in result.output

    def test_code_reviewer_blocks_edit(self) -> None:
        policy = ROLE_POLICIES["code_reviewer"]
        registry = _make_registry(policy=policy)
        tc = _make_tool_call("edit_file", {"path": "f.py", "old_string": "x", "new_string": "y"})
        result = self._execute(registry, tc)
        assert result.is_error

    def test_test_writer_write_to_tests_ok(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["test_writer"]
        registry = _make_registry(policy=policy, workspace_root=tmp_path)
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        tc = _make_tool_call(
            "write_file", {"path": str(test_dir / "test_new.py"), "content": "pass"},
        )
        result = self._execute(registry, tc)
        assert not result.is_error

    def test_test_writer_write_to_src_blocked(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["test_writer"]
        registry = _make_registry(policy=policy, workspace_root=tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        tc = _make_tool_call(
            "write_file", {"path": str(src_dir / "main.py"), "content": "x"},
        )
        result = self._execute(registry, tc)
        assert result.is_error
        assert "outside allowed directories" in result.output

    def test_feature_writer_write_to_src_ok(self, tmp_path: Path) -> None:
        policy = ROLE_POLICIES["feature_writer"]
        registry = _make_registry(policy=policy, workspace_root=tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        tc = _make_tool_call(
            "write_file", {"path": str(src_dir / "module.py"), "content": "x"},
        )
        result = self._execute(registry, tc)
        assert not result.is_error

    def test_workspace_root_property(self, tmp_path: Path) -> None:
        registry = _make_registry(workspace_root=tmp_path)
        assert registry.workspace_root == tmp_path

    def test_security_policy_property(self) -> None:
        policy = ROLE_POLICIES["code_reviewer"]
        registry = _make_registry(policy=policy)
        assert registry.security_policy is policy

    def test_policy_can_be_set_after_init(self) -> None:
        registry = _make_registry()
        assert registry.security_policy is None
        policy = ROLE_POLICIES["code_reviewer"]
        registry.security_policy = policy
        assert registry.security_policy is policy
