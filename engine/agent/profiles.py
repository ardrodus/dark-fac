"""Agent profiles for Dark Factory.

Replaces attractor_agent.profiles with a minimal profile system.
Dark Factory defines its own agent profiles rather than using
provider-specific profiles from the Attractor SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dark_factory.engine.agent.session import SessionConfig
    from dark_factory.engine.types import Tool


@dataclass
class ProviderProfile:
    """Minimal provider profile for agent configuration."""

    name: str = ""
    system_prompt: str = ""
    default_model: str = "claude-sonnet-4-5"
    supports_parallel_tool_calls: bool = True

    def apply_to_config(self, config: SessionConfig) -> SessionConfig:
        """Fill in unset config fields from this profile."""
        if not config.model:
            config.model = self.default_model
        if not config.system_prompt:
            config.system_prompt = self.system_prompt
        return config

    def get_tools(self, base_tools: list[Tool]) -> list[Tool]:
        """Return tools for this profile (default: pass through)."""
        return base_tools


_DEFAULT_PROFILE = ProviderProfile(name="default")


def get_profile(provider: str) -> ProviderProfile:  # noqa: ARG001
    """Return the agent profile for a provider (currently always default)."""
    return _DEFAULT_PROFILE
