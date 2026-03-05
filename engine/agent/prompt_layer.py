"""System prompt layering -- compose prompts from multiple sources.

Builds the final system prompt from 4 layers:

1. **System preamble**: Agent protocol pre-work context loading
   (from ``factory.agents.protocol``) plus provider base prompt.
2. **Agent role**: Role definition loaded from ``.md`` files
   (``factory/agents/``) or the ``AgentPrompt`` registry.
3. **Node prompt**: Per-node instructions from DOT graph attributes.
4. **Context variables**: Pipeline goal, context vars,
   checkpoint/resume preamble (``factory.engine.preamble``).

Plus agent protocol epilogue appended at the end.

Usage::

    from dark_factory.engine.agent.prompt_layer import build_system_prompt, PromptLayer

    prompt = build_system_prompt(
        agent_type="sa-code-quality",
        node_instruction="Focus on error handling",
        pipeline_goal="Build a REST API",
    )

Spec reference: coding-agent-loop-spec S6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Root of the factory package (factory/engine/agent/ -> factory/)
_FACTORY_ROOT = Path(__file__).resolve().parent.parent.parent

# Default directory for agent role .md files (app-type-specific agents)
_ROLE_PROMPTS_DIR = _FACTORY_ROOT / "agents"


@dataclass
class PromptLayer:
    """A single layer in the system prompt stack."""

    source: str  # "preamble", "role", "node", "context"
    content: str
    mode: str = "append"  # "append" or "replace"


def load_role_definition(
    role: str,
    *,
    search_dir: Path | None = None,
) -> str:
    """Load an agent role definition from a ``.md`` file.

    Looks for ``{role}.md`` in *search_dir* (default:
    ``factory/agents/``).  Returns the file contents
    or an empty string if the file does not exist.
    """
    prompts_dir = search_dir or _ROLE_PROMPTS_DIR
    md_path = prompts_dir / f"{role}.md"
    if md_path.is_file():
        try:
            return md_path.read_text(encoding="utf-8").strip()
        except OSError:
            logger.debug("Failed to read role definition: %s", md_path)
    return ""


def build_system_prompt(
    *,
    agent_type: str = "",
    profile_prompt: str = "",
    pipeline_goal: str = "",
    pipeline_context: dict[str, Any] | None = None,
    node_instruction: str = "",
    resume_preamble: str = "",
    task_context: dict[str, Any] | None = None,
    config: Any = None,
    role_definition: str | None = None,
    user_override: str | None = None,
    include_protocol: bool = True,
) -> str:
    """Build the final system prompt from 4 layers.

    Layers (in order):

    1. **System preamble** -- agent protocol pre-work instructions
       plus the provider profile base prompt.
    2. **Agent role** -- ``.md`` role definition for the agent type.
    3. **Node prompt** -- per-node instructions from DOT graph.
    4. **Context variables** -- pipeline goal, context vars,
       checkpoint/resume preamble.

    Plus epilogue (post-work knowledge capture) at the end.

    Args:
        agent_type: Agent role identifier (e.g. ``"sa-code-quality"``).
        profile_prompt: Provider profile's system prompt.
        pipeline_goal: The DOT graph's goal attribute.
        pipeline_context: Additional context variables from the pipeline.
        node_instruction: Per-node system_prompt or instruction attribute.
        resume_preamble: Explicit checkpoint/resume preamble text.
        task_context: Task context dict for protocol preamble generation.
        config: ``ConfigData`` for protocol preamble generation.
        role_definition: Explicit role text (overrides ``.md`` loading).
        user_override: If set, replaces the entire prompt.
        include_protocol: Whether to include agent protocol
            preamble/epilogue (default ``True``).

    Returns:
        The composed system prompt string.
    """
    # User override replaces everything
    if user_override is not None and user_override.strip():
        return user_override.strip()

    parts: list[str] = []

    # ── Layer 1: System preamble ──────────────────────────────────
    # Agent protocol pre-work context loading instructions.
    if include_protocol and agent_type:
        from dark_factory.agents.protocol import (  # noqa: PLC0415
            generate_preamble as _gen_preamble,
        )

        preamble = _gen_preamble(agent_type, task_context or {}, config)
        if preamble.strip():
            parts.append(preamble.strip())

    # Provider base prompt (still part of preamble layer).
    if profile_prompt:
        parts.append(profile_prompt.strip())

    # ── Layer 2: Agent role ───────────────────────────────────────
    # Load from .md file or use explicitly provided text.
    role_text = role_definition if role_definition is not None else ""
    if not role_text and agent_type:
        role_text = load_role_definition(agent_type)
    if role_text:
        parts.append(f"[ROLE]\n{role_text.strip()}")

    # ── Layer 3: Node prompt ──────────────────────────────────────
    if node_instruction:
        parts.append(f"[INSTRUCTION] {node_instruction.strip()}")

    # ── Layer 4: Context variables ────────────────────────────────
    if pipeline_goal:
        parts.append(f"[GOAL] {pipeline_goal}")

    if pipeline_context:
        ctx_items = [
            f"  {k}: {v}"
            for k, v in pipeline_context.items()
            if isinstance(v, (str, int, float, bool))
            and not k.startswith("_")
            and not k.startswith("parallel.")
            and not k.startswith("manager.")
            and not k.startswith("codergen.")
        ]
        if ctx_items:
            parts.append("[CONTEXT]\n" + "\n".join(ctx_items))

    # Checkpoint/resume preamble -- explicit param or _resume_preamble
    # injected into pipeline_context by the checkpoint system
    # (see factory.engine.preamble.generate_resume_preamble).
    effective_resume = resume_preamble
    if not effective_resume and pipeline_context:
        val = pipeline_context.get("_resume_preamble", "")
        if isinstance(val, str):
            effective_resume = val
    if effective_resume and effective_resume.strip():
        parts.append(effective_resume.strip())

    # ── Epilogue: post-work knowledge capture ─────────────────────
    if include_protocol and agent_type:
        from dark_factory.agents.protocol import (  # noqa: PLC0415
            generate_epilogue as _gen_epilogue,
        )

        epilogue = _gen_epilogue(agent_type, config)
        if epilogue.strip():
            parts.append(epilogue.strip())

    return "\n\n".join(parts).strip()


def layer_prompt_for_node(
    *,
    profile_prompt: str = "",
    goal: str = "",
    context: dict[str, Any] | None = None,
    node_system_prompt: str = "",
    user_system_prompt: str = "",
    agent_type: str = "",
    task_context: dict[str, Any] | None = None,
    config: Any = None,
    resume_preamble: str = "",
) -> str:
    """Convenience wrapper for building a node's system prompt.

    Called by the pipeline backend when preparing a session for
    a codergen node.
    """
    return build_system_prompt(
        agent_type=agent_type,
        profile_prompt=profile_prompt,
        pipeline_goal=goal,
        pipeline_context=context,
        node_instruction=node_system_prompt,
        resume_preamble=resume_preamble,
        task_context=task_context,
        config=config,
        user_override=user_system_prompt if user_system_prompt else None,
    )
