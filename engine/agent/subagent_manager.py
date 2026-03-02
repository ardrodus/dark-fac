"""Subagent manager for interactive tool creation.

Replaces attractor_agent.subagent_manager with a minimal stub.
The interactive subagent tools (spawn_agent, send_input, wait,
close_agent) are wired up during Session.__init__.
"""

from __future__ import annotations

from typing import Any


class SubagentManager:
    """Manages spawned subagent sessions."""


def create_interactive_tools(
    manager: SubagentManager,  # noqa: ARG001
    *,
    client: Any = None,  # noqa: ARG001
) -> list[Any]:
    """Create interactive subagent tools.

    Returns an empty list -- subagent tools are added by the
    subagent module's _add_spawn_tool() when depth allows.
    """
    return []
