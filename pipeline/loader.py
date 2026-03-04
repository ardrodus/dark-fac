"""Pipeline loader -- discovers built-in and user custom DOT files.

Searches two locations for ``*.dot`` pipeline definitions:

1. **Built-in** pipelines shipped with the factory (``factory/pipelines/*.dot``).
2. **User custom** pipelines in the project config dir (``.dark-factory/pipelines/*.dot``).

User pipelines override built-in pipelines when they share the same stem name.

Config integration: if ``.dark-factory/config.json`` contains a
``pipeline.overrides`` mapping (name → path), those entries take highest
priority and can point to arbitrary DOT files outside the standard dirs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dark_factory.core.config_manager import get_config_value, load_config

logger = logging.getLogger(__name__)

# Built-in pipelines live next to the ``factory/pipeline/`` package,
# in the sibling ``factory/pipelines/`` directory.
_BUILTINS_DIR = Path(__file__).resolve().parent.parent / "pipelines"

_USER_PIPELINES_REL = Path(".dark-factory") / "pipelines"


def _collect_dot_files(directory: Path) -> dict[str, Path]:
    """Return ``{stem: path}`` for every ``*.dot`` file in *directory*."""
    if not directory.is_dir():
        return {}
    return {p.stem: p for p in sorted(directory.glob("*.dot"))}


def discover_pipelines(
    *,
    project_root: Path | None = None,
    builtins_dir: Path | None = None,
    workspace_pipeline_dir: Path | None = None,
) -> dict[str, Path]:
    """Discover available pipeline DOT files.

    Resolution order (last wins):

    1. Built-in pipelines from ``factory/pipelines/*.dot``.
    2. User custom pipelines from ``.dark-factory/pipelines/*.dot``.
    3. Explicit overrides from ``pipeline.overrides`` in config.json.
    4. Workspace pipelines from ``{workspace}/pipeline/*.dot`` (highest priority).

    Args:
        project_root: Root directory containing ``.dark-factory/``.
            Defaults to cwd.
        builtins_dir: Override for the built-in pipelines directory.
            Useful for testing.
        workspace_pipeline_dir: Workspace-scoped pipeline directory.
            When set, DOT files here take highest priority.

    Returns:
        Mapping of pipeline name (stem) to resolved ``Path``.
    """
    root = (project_root or Path.cwd()).resolve()
    builtin_path = builtins_dir if builtins_dir is not None else _BUILTINS_DIR

    # 1. Built-in
    pipelines = _collect_dot_files(builtin_path)
    logger.debug("Built-in pipelines: %s", list(pipelines))

    # 2. User custom
    user_dir = root / _USER_PIPELINES_REL
    user_pipelines = _collect_dot_files(user_dir)
    if user_pipelines:
        logger.debug("User pipelines (override): %s", list(user_pipelines))
        pipelines.update(user_pipelines)

    # 3. Config overrides
    try:
        cfg = load_config(root)
        raw_overrides: Any = get_config_value(cfg, "pipeline.overrides")
        if isinstance(raw_overrides, dict):
            for name, path_str in raw_overrides.items():
                if not isinstance(name, str) or not isinstance(path_str, str):
                    continue
                p = Path(path_str)
                if not p.is_absolute():
                    p = root / p
                if p.is_file():
                    pipelines[name] = p
                    logger.debug("Config override: %s -> %s", name, p)
                else:
                    logger.warning(
                        "Pipeline override %r points to missing file: %s",
                        name,
                        p,
                    )
    except Exception:
        logger.debug("No config overrides applied (config load failed or absent)")

    # 4. Workspace pipelines (highest priority)
    if workspace_pipeline_dir:
        ws_pipelines = _collect_dot_files(workspace_pipeline_dir)
        if ws_pipelines:
            logger.debug("Workspace pipelines (override): %s", list(ws_pipelines))
            pipelines.update(ws_pipelines)

    return pipelines
