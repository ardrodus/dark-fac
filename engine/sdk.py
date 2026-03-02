"""High-level SDK for running Dark Factory pipelines as library calls.

This module provides the simple, one-call API for embedding pipelines
inside larger applications (e.g., Dark Factory auto mode).

Usage::

    from dark_factory.engine.sdk import execute

    result = await execute(
        "factory/pipelines/dark_forge.dot",
        model="claude-sonnet-4-5",
        context={"issue": issue_data},
    )

    # With callbacks
    result = await execute(
        "factory/pipelines/crucible.dot",
        context={"base_sha": "abc123", "head_sha": "def456"},
        on_event=my_event_handler,
        on_human_gate=my_approval_callback,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dark_factory.engine.events import PipelineEvent
from dark_factory.engine.runner import PipelineResult


@dataclass
class ExecuteConfig:
    """Configuration for pipeline execution."""

    # Model (passed to ClaudeCodeBackend)
    model: str | None = None

    # Pipeline context (variables available to nodes via $variable)
    context: dict[str, Any] = field(default_factory=dict)

    # Stylesheet for model assignment per node
    stylesheet_path: str | None = None

    # Execution options
    logs_dir: str | None = None

    # Callbacks
    on_event: Callable[[PipelineEvent], None] | None = None
    on_human_gate: Callable[[str, str], str] | None = None


async def execute(
    dotfile: str | Path,
    *,
    model: str | None = None,
    context: dict[str, Any] | None = None,
    stylesheet_path: str | None = None,
    logs_dir: str | None = None,
    on_event: Callable[[PipelineEvent], None] | None = None,
    on_human_gate: Callable[[str, str], str] | None = None,
) -> PipelineResult:
    """Execute a DOT pipeline and return the result.

    This is the one-call API. Parse, validate, configure backend,
    and run -- all in one function. Uses ClaudeCodeBackend (claude CLI)
    for all LLM calls.

    Args:
        dotfile: Path to the DOT pipeline file.
        model: Model ID for ClaudeCodeBackend (optional).
        context: Initial pipeline context (variables for $expansion).
        stylesheet_path: Path to a .styles file for per-node model assignment.
        logs_dir: Directory for logs and checkpoints.
        on_event: Callback invoked for each pipeline event.
        on_human_gate: Callback for human gate nodes. Receives (node_id, prompt),
            returns the human's answer. If None, human gates auto-approve.

    Returns:
        PipelineResult with status, context, completed nodes, and any error.

    Raises:
        FileNotFoundError: If dotfile doesn't exist.
        ValueError: If the DOT file fails validation.
    """
    from dark_factory.engine import (  # noqa: PLC0415
        HandlerRegistry,
        parse_dot,
        register_default_handlers,
    )
    from dark_factory.engine import run_pipeline as _run_pipeline  # noqa: PLC0415
    from dark_factory.engine.claude_backend import (  # noqa: PLC0415
        ClaudeCodeBackend,
        ClaudeCodeConfig,
    )
    from dark_factory.engine.config import load_engine_config  # noqa: PLC0415
    from dark_factory.engine.validation import validate_or_raise  # noqa: PLC0415

    # --- Load factory config (fallback values) ---
    engine_cfg = load_engine_config(Path(dotfile).parent)

    # --- Parse ---
    path = Path(dotfile)
    if not path.exists():
        raise FileNotFoundError(f"Pipeline file not found: {dotfile}")

    source = path.read_text(encoding="utf-8")
    graph = parse_dot(source)

    # --- Validate ---
    validate_or_raise(graph)

    # --- Apply Stylesheet ---
    # Priority: explicit stylesheet_path > config stylesheet
    if stylesheet_path:
        from dark_factory.engine.stylesheet import apply_stylesheet  # noqa: PLC0415

        style_path = Path(stylesheet_path)
        if style_path.exists():
            graph.model_stylesheet = style_path.read_text(encoding="utf-8")
            apply_stylesheet(graph)
    elif engine_cfg.model_stylesheet and not graph.model_stylesheet:
        from dark_factory.engine.stylesheet import apply_stylesheet  # noqa: PLC0415

        graph.model_stylesheet = engine_cfg.model_stylesheet
        apply_stylesheet(graph)

    # --- Set up Backend (ClaudeCodeBackend) ---
    # Priority: explicit model arg > config model
    resolved_model = model or engine_cfg.model
    resolved_claude_path = engine_cfg.claude_path
    cfg = ClaudeCodeConfig(model=resolved_model, claude_path=resolved_claude_path)
    backend = ClaudeCodeBackend(cfg)

    # --- Set up Handlers ---
    registry = HandlerRegistry()
    register_default_handlers(registry, codergen_backend=backend)

    # --- Human Gate Callback ---
    if on_human_gate:
        from dark_factory.engine.handlers import (  # noqa: PLC0415
            CallbackInterviewer,
            HumanHandler,
        )

        _gate_cb = on_human_gate

        async def _human_callback(
            text: str, options: list[str] | None, stage: str | None,
        ) -> str:
            return _gate_cb(stage or "", text)

        interviewer = CallbackInterviewer(callback=_human_callback)
        registry.register("wait.human", HumanHandler(interviewer=interviewer))

    # --- Logs ---
    logs_root = None
    if logs_dir:
        logs_root = Path(logs_dir)
        logs_root.mkdir(parents=True, exist_ok=True)

    # --- Execute ---
    return await _run_pipeline(
        graph,
        registry,
        context=context,
        logs_root=logs_root,
        on_event=on_event,
    )
