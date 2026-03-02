"""Engine type definitions — local replacements for attractor_llm types.

These are the boundary types between the engine and the LLM backend.
ClaudeCodeBackend is the primary backend; these types exist for the
engine's internal protocol (message history, tool definitions, etc.).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ------------------------------------------------------------------ #
# Enums
# ------------------------------------------------------------------ #


class Role(StrEnum):
    """Message role in the conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ContentPartKind(StrEnum):
    """Kind of content part within a message."""

    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


class FinishReason(StrEnum):
    """Reason the LLM stopped generating."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


# ------------------------------------------------------------------ #
# Content and Messages
# ------------------------------------------------------------------ #


@dataclass
class ContentPart:
    """A single content part within a message (text, tool call, or tool result)."""

    kind: ContentPartKind = ContentPartKind.TEXT
    text: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, Any] | str | None = None
    output: str = ""
    is_error: bool = False

    @classmethod
    def tool_result_part(
        cls,
        *,
        tool_call_id: str,
        name: str,
        output: str,
        is_error: bool,
    ) -> ContentPart:
        return cls(
            kind=ContentPartKind.TOOL_RESULT,
            tool_call_id=tool_call_id,
            name=name,
            output=output,
            is_error=is_error,
        )

    @classmethod
    def tool_call_part(
        cls,
        tool_call_id: str,
        name: str,
        arguments: dict[str, Any] | str,
    ) -> ContentPart:
        return cls(
            kind=ContentPartKind.TOOL_CALL,
            tool_call_id=tool_call_id,
            name=name,
            arguments=arguments,
        )


@dataclass
class Message:
    """A message in the conversation history."""

    role: Role = Role.USER
    content: list[ContentPart] = field(default_factory=list)

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(
            role=Role.USER,
            content=[ContentPart(kind=ContentPartKind.TEXT, text=text)],
        )

    @classmethod
    def assistant(cls, text: str) -> Message:
        return cls(
            role=Role.ASSISTANT,
            content=[ContentPart(kind=ContentPartKind.TEXT, text=text)],
        )

    @property
    def text(self) -> str | None:
        """Return the text of the first TEXT content part, or None."""
        for part in self.content:
            if part.kind == ContentPartKind.TEXT and part.text is not None:
                return part.text
        return None


@dataclass
class Tool:
    """Tool definition with schema and execute handler."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    execute: Callable[..., Any] | None = None


@dataclass
class Usage:
    """Token usage tracking."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    def model_dump(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


# ------------------------------------------------------------------ #
# LLM Request / Response
# ------------------------------------------------------------------ #


@dataclass
class Request:
    """LLM completion request."""

    model: str = ""
    provider: str | None = None
    messages: list[Message] = field(default_factory=list)
    system: str | None = None
    tools: list[Tool] | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    provider_options: dict[str, Any] | None = None


@dataclass
class Response:
    """LLM completion response."""

    message: Message = field(default_factory=Message)
    text: str | None = None
    tool_calls: list[ContentPart] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    id: str = ""
    model: str = ""
    provider: str = ""
    finish_reason: FinishReason | None = None

    def __post_init__(self) -> None:
        """Auto-populate tool_calls and text from message content if not set."""
        if not self.tool_calls and self.message.content:
            self.tool_calls = [
                p for p in self.message.content
                if p.kind == ContentPartKind.TOOL_CALL
            ]
        if self.text is None and self.message.content:
            text_parts = [
                p.text for p in self.message.content
                if p.kind == ContentPartKind.TEXT and p.text
            ]
            if text_parts:
                self.text = "".join(text_parts)


# ------------------------------------------------------------------ #
# Retry Policy (replaces attractor_llm.retry.RetryPolicy)
# ------------------------------------------------------------------ #


@dataclass
class RetryPolicy:
    """Retry backoff policy with exponential delay and optional jitter."""

    max_retries: int = 0
    initial_delay: float = 0.0
    backoff_factor: float = 1.0
    max_delay: float = 0.0
    jitter: bool = True

    def compute_delay(self, attempt: int) -> float:
        delay = self.initial_delay * (self.backoff_factor ** attempt)
        if self.max_delay > 0:
            delay = min(delay, self.max_delay)
        if self.jitter:
            delay *= random.uniform(0.5, 1.5)  # noqa: S311
        return delay


# ------------------------------------------------------------------ #
# Model Catalog (replaces attractor_llm.catalog)
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class ModelInfo:
    """Basic model metadata."""

    context_window: int = 200_000


_MODEL_CATALOG: dict[str, ModelInfo] = {
    "claude-sonnet-4-5": ModelInfo(context_window=200_000),
    "claude-opus-4-5": ModelInfo(context_window=200_000),
    "claude-haiku-3-5": ModelInfo(context_window=200_000),
}


def get_model_info(model: str) -> ModelInfo | None:
    """Return model info for known models, or None."""
    return _MODEL_CATALOG.get(model)


# ------------------------------------------------------------------ #
# Client (placeholder -- not used when ClaudeCodeBackend is active)
# ------------------------------------------------------------------ #


class Client:
    """Placeholder LLM client.

    Not used in production (ClaudeCodeBackend handles LLM calls via CLI).
    Exists so that engine code type-checks without external deps.
    """

    async def complete(
        self, request: Request, abort_signal: Any = None,
    ) -> Response:
        raise NotImplementedError(
            "Use ClaudeCodeBackend instead of Client.complete()"
        )

    def register_adapter(self, name: str, adapter: Any) -> None:
        """No-op -- providers managed externally by Claude Code."""

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


# ------------------------------------------------------------------ #
# Error types (replaces attractor_llm.errors)
# ------------------------------------------------------------------ #


class ConfigurationError(Exception):
    """LLM configuration error."""


class AuthenticationError(Exception):
    """Authentication failure (401)."""


class AccessDeniedError(Exception):
    """Access denied (403)."""
