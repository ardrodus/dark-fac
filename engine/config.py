"""Engine configuration bridge -- reads from .dark-factory/config.json.

Provides :class:`EngineConfig`, a frozen dataclass that the engine modules
consume, populated from the centralised factory config system.

Usage::

    from dark_factory.engine.config import load_engine_config

    cfg = load_engine_config()          # from cwd
    cfg = load_engine_config(start=p)   # from explicit path
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from dark_factory.core.config_manager import (
    get_config_value,
    load_config,
    resolve_config_dir,
)

logger = logging.getLogger(__name__)

_STYLESHEET_FILENAME = "model-stylesheet.css"

# Default stylesheet shipped with the engine.  Applied when no project-level
# stylesheet is found in ``.dark-factory/model-stylesheet.css``.
_DEFAULT_STYLESHEET = """\
/* Default model stylesheet -- assigns a sensible base model to all nodes. */
* { llm_model: claude-sonnet-4-5; }
"""


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Resolved engine configuration from .dark-factory/config.json."""

    model: str = ""
    claude_path: str = "claude"
    deploy_strategy: str = "console"
    sentinel_scan_mode: str = "standard"
    pipeline_timeout: int = 600
    max_concurrent_subprocesses: int = 3
    model_stylesheet: str = ""


def _load_stylesheet(config_dir: Path) -> str:
    """Load model stylesheet from ``.dark-factory/model-stylesheet.css``.

    Returns the file contents if present, otherwise the built-in default.
    """
    path = config_dir / _STYLESHEET_FILENAME
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
            logger.debug("Loaded model stylesheet from %s", path)
            return content
        except OSError as exc:
            logger.warning("Failed to read stylesheet %s: %s", path, exc)
    return _DEFAULT_STYLESHEET


def load_engine_config(start: Path | None = None) -> EngineConfig:
    """Load engine configuration from the factory config system.

    Reads values from ``.dark-factory/config.json`` (merged with env vars
    and ``.env`` per :func:`~factory.core.config_manager.load_config`),
    then loads the model stylesheet from
    ``.dark-factory/model-stylesheet.css`` if present.

    Args:
        start: Directory to start searching for ``.dark-factory/``.
            Defaults to cwd.

    Returns:
        A frozen :class:`EngineConfig` with all values resolved.
    """
    cfg = load_config(start)

    model = get_config_value(cfg, "engine.model") or ""
    claude_path = get_config_value(cfg, "engine.claude_path") or "claude"
    deploy_strategy = get_config_value(cfg, "engine.deploy_strategy") or "console"
    sentinel_scan_mode = get_config_value(cfg, "sentinel.scan_mode") or "standard"

    raw_timeout = get_config_value(cfg, "engine.pipeline_timeout")
    if isinstance(raw_timeout, (int, float)) and raw_timeout > 0:
        pipeline_timeout = int(raw_timeout)
    else:
        pipeline_timeout = 600

    raw_max_sub = get_config_value(cfg, "engine.max_concurrent_subprocesses")
    if isinstance(raw_max_sub, (int, float)) and raw_max_sub > 0:
        max_concurrent_subprocesses = int(raw_max_sub)
    else:
        max_concurrent_subprocesses = 3

    config_dir = resolve_config_dir(start)
    stylesheet = _load_stylesheet(config_dir)

    return EngineConfig(
        model=str(model),
        claude_path=str(claude_path),
        deploy_strategy=str(deploy_strategy),
        sentinel_scan_mode=str(sentinel_scan_mode),
        pipeline_timeout=pipeline_timeout,
        max_concurrent_subprocesses=max_concurrent_subprocesses,
        model_stylesheet=stylesheet,
    )
