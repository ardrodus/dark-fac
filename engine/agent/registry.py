"""Tool registry for the Coding Agent Loop.

Manages registration, lookup, validation, and execution of tools.
The registry is the central dispatch point for all tool calls from the LLM.

Also defines per-node security policies that confine tool access based on
the pipeline role (test_writer, feature_writer, code_reviewer).

Spec reference: coding-agent-loop §3.8.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from factory.engine.agent.events import EventEmitter, EventKind, SessionEvent
from factory.engine.agent.truncation import TruncationLimits, truncate_output
from factory.engine.types import ContentPart, ContentPartKind, Tool

# ------------------------------------------------------------------ #
# Security policies for per-node tool confinement
# ------------------------------------------------------------------ #

# Tool name categories
_READ_TOOLS: frozenset[str] = frozenset({
    "read_file", "read_many_files", "grep", "glob", "list_dir",
})
_WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "apply_patch",
})
_SHELL_TOOLS: frozenset[str] = frozenset({"shell"})
_ALL_TOOLS: frozenset[str] = _READ_TOOLS | _WRITE_TOOLS | _SHELL_TOOLS


@dataclass(frozen=True)
class SecurityPolicy:
    """Per-node tool security policy.

    Controls which tools a node can use and which workspace-relative
    directories it can write to.

    Attributes:
        allowed_tools: Whitelist of tool names this node may invoke.
        writable_paths: Workspace-relative directory prefixes where write
            operations are permitted (e.g. ``("tests/",)``).  Empty tuple
            means writes allowed anywhere within the workspace.
        read_only: If True, no write/edit/shell tools are permitted
            (shorthand — ``allowed_tools`` already enforces this, but
            read_only makes the intent explicit for clarity).
    """

    allowed_tools: frozenset[str] = _ALL_TOOLS
    writable_paths: tuple[str, ...] = ()
    read_only: bool = False


ROLE_POLICIES: dict[str, SecurityPolicy] = {
    "test_writer": SecurityPolicy(
        allowed_tools=_READ_TOOLS | _WRITE_TOOLS | _SHELL_TOOLS,
        writable_paths=("tests/",),
    ),
    "feature_writer": SecurityPolicy(
        allowed_tools=_READ_TOOLS | _WRITE_TOOLS | _SHELL_TOOLS,
        writable_paths=("src/", "lib/"),
    ),
    "code_reviewer": SecurityPolicy(
        allowed_tools=_READ_TOOLS,
        read_only=True,
    ),
}


def resolve_security_policy(
    node_id: str,
    node_attrs: dict[str, Any] | None = None,
) -> SecurityPolicy | None:
    """Resolve the security policy for a pipeline node.

    Resolution order:
    1. Explicit ``role`` attribute on the node.
    2. Node ID substring match against known role names.

    Returns ``None`` if no policy applies (unrestricted).
    """
    attrs = node_attrs or {}
    role = attrs.get("role", "")
    if role and role in ROLE_POLICIES:
        return ROLE_POLICIES[role]

    # Fall back to node ID matching
    node_lower = node_id.lower().replace("-", "_")
    for role_name, policy in ROLE_POLICIES.items():
        if role_name in node_lower:
            return policy
    return None


def _extract_path_arg(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Extract the file/directory path argument from a tool call.

    Returns the path string, or None if no path argument exists.
    """
    if tool_name in ("write_file", "edit_file", "read_file"):
        return arguments.get("path")
    if tool_name == "apply_patch":
        return arguments.get("working_dir")
    return None


def check_security_policy(
    tool_name: str,
    arguments: dict[str, Any],
    policy: SecurityPolicy,
    workspace_root: Path | None,
) -> str | None:
    """Check whether a tool call is allowed by the security policy.

    Returns ``None`` if allowed, or an error message string if denied.
    """
    # 1. Tool whitelist check
    if tool_name not in policy.allowed_tools:
        return (
            f"Security policy violation: tool '{tool_name}' is not allowed "
            f"for this node. Allowed tools: {sorted(policy.allowed_tools)}"
        )

    # 2. Write-path confinement check
    if (
        policy.writable_paths
        and workspace_root is not None
        and tool_name in _WRITE_TOOLS
    ):
        raw_path = _extract_path_arg(tool_name, arguments)
        if raw_path is not None:
            # Normalize to forward-slash relative path within workspace
            try:
                resolved = Path(raw_path).resolve()
                ws_resolved = workspace_root.resolve()
                rel = resolved.relative_to(ws_resolved)
            except (ValueError, OSError):
                return (
                    f"Security policy violation: path '{raw_path}' is "
                    f"outside workspace '{workspace_root}'"
                )

            # Convert to posix for prefix matching
            rel_posix = PurePosixPath(rel).as_posix()
            if not any(
                rel_posix.startswith(prefix.rstrip("/"))
                for prefix in policy.writable_paths
            ):
                return (
                    f"Security policy violation: write to '{rel_posix}' "
                    f"is outside allowed directories "
                    f"{list(policy.writable_paths)} for this node"
                )

    return None

# ------------------------------------------------------------------ #
# Lightweight argument schema validation  (Spec §5.5)
# ------------------------------------------------------------------ #

# Maps JSON Schema type names to Python types for top-level checking.
_JSON_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_tool_arguments(
    arguments: dict[str, Any],
    schema: dict[str, Any],
) -> str | None:
    """Validate *arguments* against a JSON-Schema-style *schema*.

    Performs two top-level checks:
    1. All ``required`` fields are present.
    2. Provided values match the declared ``type`` (top-level only).

    Returns ``None`` when valid, or an error message string when not.
    """
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    # 1. Required fields
    missing = [f for f in required if f not in arguments]
    if missing:
        return f"Missing required argument(s): {', '.join(missing)}"

    # 2. Type checks on provided values
    for key, value in arguments.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            continue  # extra keys are tolerated
        expected_type_name = prop_schema.get("type")
        if expected_type_name is None:
            continue
        expected_types = _JSON_TYPE_MAP.get(expected_type_name)
        if expected_types is None:
            continue
        # JSON booleans are distinct from ints in Python, but isinstance(True, int)
        # is True.  Exclude bool when checking for int/number.
        if expected_type_name in ("integer", "number") and isinstance(value, bool):
            return f"Argument '{key}' has type bool, expected {expected_type_name}"
        if not isinstance(value, expected_types):
            actual = type(value).__name__
            return f"Argument '{key}' has type {actual}, expected {expected_type_name}"

    return None


class ToolRegistry:
    """Registry for managing and executing tools.

    The tool execution pipeline follows this sequence:
    1. Lookup: Find the tool by name
    2. Validate: Check input against JSON Schema (basic)
    3. Execute: Run the tool's execute handler
    4. Truncate: Apply output truncation limits
    5. Emit: Fire tool.call_start and tool.call_end events
    6. Return: Build tool result ContentPart

    Spec reference: coding-agent-loop §3.8.
    """

    def __init__(
        self,
        event_emitter: EventEmitter | None = None,
        tool_output_limits: dict[str, int] | None = None,
        tool_line_limits: dict[str, int] | None = None,
        *,
        supports_parallel_tool_calls: bool = True,
        security_policy: SecurityPolicy | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._emitter = event_emitter
        self._output_limits = tool_output_limits
        self._line_limits = tool_line_limits
        self.supports_parallel_tool_calls = supports_parallel_tool_calls
        self.security_policy = security_policy
        self.workspace_root = workspace_root

    def register(self, tool: Tool) -> None:
        """Register a tool. Overwrites if name already exists."""
        self._tools[tool.name] = tool

    def register_many(self, tools: list[Tool]) -> None:
        """Register multiple tools at once."""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        """Remove a tool by name. No-op if not found."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def definitions(self) -> list[Tool]:
        """Return all registered tools (for sending to LLM)."""
        return list(self._tools.values())

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    async def execute_tool_call(self, tool_call: ContentPart) -> ContentPart:
        """Execute a single tool call and return the result.

        Handles the full pipeline: lookup → validate → execute → truncate → emit.

        Args:
            tool_call: A ContentPart with kind=TOOL_CALL.

        Returns:
            A ContentPart with kind=TOOL_RESULT containing the output.
        """
        assert tool_call.kind == ContentPartKind.TOOL_CALL  # noqa: S101

        tool_name = tool_call.name or ""
        tool_call_id = tool_call.tool_call_id or ""
        arguments = tool_call.arguments

        # Parse arguments if string
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        if not isinstance(arguments, dict):
            arguments = {}

        # Emit start event
        if self._emitter:
            await self._emitter.emit(
                SessionEvent(
                    kind=EventKind.TOOL_CALL_START,
                    data={"tool": tool_name, "call_id": tool_call_id, "arguments": arguments},
                )
            )

        # Security policy enforcement (per-node confinement)
        if self.security_policy is not None:
            policy_error = check_security_policy(
                tool_name, arguments, self.security_policy, self.workspace_root,
            )
            if policy_error:
                output = f"Error: {policy_error}"
                raw_output_str = output
                is_error = True

                if self._emitter:
                    await self._emitter.emit(
                        SessionEvent(
                            kind=EventKind.TOOL_CALL_END,
                            data={
                                "tool": tool_name,
                                "call_id": tool_call_id,
                                "is_error": True,
                                "output": raw_output_str,
                            },
                        )
                    )

                return ContentPart.tool_result_part(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    output=output,
                    is_error=is_error,
                )

        # Lookup
        tool = self.get(tool_name)
        raw_output_str = ""
        if tool is None:
            output = f"Error: Unknown tool '{tool_name}'"
            raw_output_str = output
            is_error = True
        elif tool.execute is None:
            output = f"Error: Tool '{tool_name}' has no execute handler"
            raw_output_str = output
            is_error = True
        else:
            # Validate arguments against tool schema (Spec §5.5)
            validation_error = validate_tool_arguments(arguments, tool.parameters)
            if validation_error:
                output = f"Error: Invalid arguments for '{tool_name}': {validation_error}"
                raw_output_str = output
                is_error = True
            else:
                # Execute
                try:
                    raw_output = await tool.execute(**arguments)
                    is_error = False

                    # Truncate (raw preserved for event; truncated goes to LLM)
                    raw_output_str = str(raw_output)
                    limits = TruncationLimits.for_tool(
                        tool_name, self._output_limits, self._line_limits
                    )
                    output, was_truncated = truncate_output(raw_output_str, limits)
                    if was_truncated:
                        output += "\n[output was truncated]"

                except Exception as exc:  # noqa: BLE001
                    # Send only the exception message to the LLM, not the full
                    # traceback (which leaks internal paths and implementation details).
                    output = f"Error executing {tool_name}: {type(exc).__name__}: {exc}"
                    raw_output_str = output
                    is_error = True

        # Emit end event (carries full untruncated output per Spec §2.9, §5.1)
        if self._emitter:
            await self._emitter.emit(
                SessionEvent(
                    kind=EventKind.TOOL_CALL_END,
                    data={
                        "tool": tool_name,
                        "call_id": tool_call_id,
                        "is_error": is_error,
                        "output": raw_output_str,
                    },
                )
            )

        return ContentPart.tool_result_part(
            tool_call_id=tool_call_id,
            name=tool_name,
            output=output,
            is_error=is_error,
        )

    async def execute_tool_calls(self, tool_calls: list[ContentPart]) -> list[ContentPart]:
        """Execute multiple tool calls, optionally in parallel. Spec §5.7.

        When ``supports_parallel_tool_calls`` is True (default), multiple
        tool calls are executed concurrently via asyncio.gather().
        When False, they are executed sequentially.

        Results are returned in the same order as the input tool calls.
        Partial failures are handled: successful tools return normally,
        failed tools return is_error=True results.

        Args:
            tool_calls: List of ContentParts with kind=TOOL_CALL.

        Returns:
            List of ContentParts with kind=TOOL_RESULT, in the same order.
        """
        if len(tool_calls) <= 1 or not self.supports_parallel_tool_calls:
            # Sequential execution: single call, or parallel not supported.
            return [await self.execute_tool_call(tc) for tc in tool_calls]

        # Multiple tool calls: execute concurrently
        # NOTE: Filesystem-mutating tools (edit_file, write_file) may race when
        # targeting the same file. Per Spec §5.7 we execute concurrently; callers
        # should avoid issuing conflicting writes in the same batch.
        results = await asyncio.gather(
            *(self.execute_tool_call(tc) for tc in tool_calls),
            return_exceptions=True,
        )

        # Convert any unexpected exceptions to error results
        final: list[ContentPart] = []
        for i, result in enumerate(results):
            if isinstance(result, (KeyboardInterrupt, SystemExit)):
                raise result
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                tc = tool_calls[i]
                final.append(
                    ContentPart.tool_result_part(
                        tool_call_id=tc.tool_call_id or "",
                        name=tc.name or "",
                        output=f"Error: {type(result).__name__}: {result}",
                        is_error=True,
                    )
                )
            else:
                final.append(result)
        return final
