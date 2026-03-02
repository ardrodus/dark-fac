"""Agent subsystem -- coding agent loop for Dark Factory.

Provides the core Session, tools, event system, and environment
abstraction for autonomous coding agents.

Modules:
    session: Core agentic loop (Session, SessionConfig)
    tools: Developer tools (read_file, write_file, edit_file, shell, grep, glob)
    registry: Tool registry and execution pipeline
    events: Event system (EventEmitter, EventKind, SessionEvent)
    abort: Cooperative cancellation (AbortSignal)
    environment: Execution environment abstraction (Local, Docker)
    prompt_layer: System prompt layering
    truncation: Tool output truncation engine
    apply_patch: Unified diff patch parser and applicator
    subagent: Subagent spawning for delegated tasks
"""

from dark_factory.engine.agent.abort import AbortSignal
from dark_factory.engine.agent.events import EventEmitter, EventKind, SessionEvent
from dark_factory.engine.agent.registry import ToolRegistry
from dark_factory.engine.agent.session import Session, SessionConfig, SessionState, SteeringTurn
from dark_factory.engine.agent.tools import ALL_CORE_TOOLS
from dark_factory.engine.agent.truncation import TruncationLimits, truncate_output

__all__ = [
    "ALL_CORE_TOOLS",
    "AbortSignal",
    "EventEmitter",
    "EventKind",
    "Session",
    "SessionConfig",
    "SessionEvent",
    "SessionState",
    "SteeringTurn",
    "ToolRegistry",
    "TruncationLimits",
    "truncate_output",
]
