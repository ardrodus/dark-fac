"""Claude model detection, prompting, and persistence.

Ports detect_claude_model(), prompt_claude_model(), save_claude_model(),
and get_claude_model() from pipeline-log.sh.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL_CHOICES: list[tuple[str, str]] = [
    ("claude-sonnet-4-20250514", "Anthropic API"),
    ("claude-opus-4-20250514", "Anthropic API"),
    ("us.anthropic.claude-sonnet-4-20250514-v1:0", "AWS Bedrock"),
    ("us.anthropic.claude-opus-4-20250514-v1:0", "AWS Bedrock"),
]

_cached_model: str | None = None  # module-level cache


def _read_json_key(path: Path, *keys: str) -> str:
    """Return first non-empty string value for *keys* in a JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    if not isinstance(data, dict):
        return ""
    for k in keys:
        val = data.get(k)
        if isinstance(val, str) and val and val != "null":
            return val
    return ""


def _claude_settings_paths() -> list[Path]:
    """Return candidate Claude Code settings file paths (cross-platform)."""
    paths: list[Path] = []
    settings = Path.home() / ".claude" / "settings.json"
    if settings.is_file():
        paths.append(settings)
    for var in ("APPDATA", "LOCALAPPDATA"):
        raw = os.environ.get(var, "")
        if raw:
            candidate = Path(raw) / "claude" / "settings.json"
            if candidate.is_file():
                paths.append(candidate)
    return paths


def _detect_from_config() -> str:
    """Check .dark-factory/config.json for claude_model."""
    from dark_factory.core.config_manager import resolve_config_path  # noqa: PLC0415
    return _read_json_key(resolve_config_path(), "claude_model")


def detect_claude_model() -> str:
    """Auto-detect the Claude model. Does NOT cache -- use get_claude_model().

    Order: CLAUDE_MODEL env -> CLAUDE_CODE_DEFAULT_MODEL env ->
    .dark-factory/config.json -> Claude Code settings files -> "".
    """
    for var in ("CLAUDE_MODEL", "CLAUDE_CODE_DEFAULT_MODEL"):
        val = os.environ.get(var, "")
        if val:
            logger.debug("Model from %s env: %s", var, val)
            return val
    config_model = _detect_from_config()
    if config_model:
        logger.debug("Model from config.json: %s", config_model)
        return config_model
    for sf in _claude_settings_paths():
        m = _read_json_key(sf, "model", "default_model", "preferredModel")
        if m:
            logger.debug("Model from settings %s: %s", sf, m)
            return m
    logger.debug("No Claude model detected")
    return ""


def prompt_claude_model() -> str:
    """Interactively prompt the user to select a Claude model.

    Returns the chosen model string, or "" if the user cancels.
    """
    if not sys.stdin.isatty():
        return ""
    n = len(_MODEL_CHOICES)
    print("\n  Claude Model Configuration\n")
    print("  Dark Factory could not auto-detect which Claude model to use.")
    print("  All pipeline agents need a --model flag for reliable operation.\n")
    print("  Common models:")
    for i, (model_id, label) in enumerate(_MODEL_CHOICES, 1):
        print(f"    {i}) {model_id}  ({label})")
    print(f"    {n + 1}) Enter custom model ID\n")
    try:
        choice = input(f"  Select [1-{n + 1}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= n:
            return _MODEL_CHOICES[idx - 1][0]
        if idx == n + 1:
            try:
                return input("  Enter model ID: ").strip()
            except (EOFError, KeyboardInterrupt):
                return ""
    print("  Invalid selection.")
    return ""


def save_claude_model(model: str) -> None:
    """Persist *model* to .dark-factory/config.json and update the cache."""
    from dark_factory.core.config_manager import (  # noqa: PLC0415
        load_config,
        save_config,
        set_config_value,
    )
    global _cached_model  # noqa: PLW0603
    cfg = load_config()
    set_config_value(cfg, "claude_model", model)
    save_config(cfg)
    _cached_model = model
    logger.info("Claude model saved: %s", model)


def get_claude_model() -> str:
    """Return the configured Claude model, caching after first call."""
    global _cached_model  # noqa: PLW0603
    if _cached_model is not None:
        return _cached_model
    _cached_model = detect_claude_model()
    return _cached_model
