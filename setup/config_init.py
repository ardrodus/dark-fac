"""Config initialization — creates .dark-factory/config.json and repo entries.

Architecture
~~~~~~~~~~~~
Called by the orchestrator during phase [9/10] Configuration.

- :func:`init_config` — idempotent creation of ``.dark-factory/config.json``
  with initial structure (version, auth_method, repos[], agents{}).
  Also creates the ``.secrets/`` directory with restricted permissions.

- :func:`add_repo_to_config` — appends a repo entry to ``repos[]``.
  Deactivates all existing repos first.  Stages the full
  :class:`~project_analyzer.AnalysisResult` as ``workspace_config.analysis``
  so downstream workspace creation has access to language, framework, etc.

The config file is the global state shared across onboarding, workspace
management, and pipeline execution.  Repo-specific settings go under
``repos[].workspace_config``, NOT at the top level.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dark_factory.setup.project_analyzer import AnalysisResult

_CONFIG_VERSION = 1

def init_config(
    *,
    force: bool = False,
    start: Path | None = None,
) -> Path:
    """Create ``.dark-factory/config.json`` with initial structure.

    Idempotent: skips creation if config already exists unless *force* is True.
    Returns the config file path.
    """
    from dark_factory.core.config_manager import resolve_config_dir  # noqa: PLC0415

    config_dir = resolve_config_dir(start)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    # Create secrets directory with restricted permissions (700 on POSIX)
    secrets_dir = config_dir / ".secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        secrets_dir.chmod(stat.S_IRWXU)

    if config_file.is_file() and not force:
        return config_file

    data = {
        "version": _CONFIG_VERSION,
        "auth_method": "",
        "repos": [],
        "agents": {},
    }
    config_file.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_file


def add_repo_to_config(
    repo: str,
    app_type: str = "",
    analysis: AnalysisResult | None = None,
    *,
    start: Path | None = None,
) -> None:
    """Append a repo entry to the ``repos`` array in config.json.

    Deactivates all existing repos first, then adds the new one as active.
    If *analysis* is provided, its fields are staged in ``workspace_config``.
    """
    from dark_factory.core.config_manager import (  # noqa: PLC0415
        load_config,
        save_config,
    )

    cfg = load_config(start)
    repos = cfg.data.get("repos")
    if not isinstance(repos, list):
        repos = []
        cfg.data["repos"] = repos

    # Deactivate all existing repos
    for r in repos:
        if isinstance(r, dict):
            r["active"] = False

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Global repo entry — minimal: just identity + activation state
    entry: dict[str, object] = {
        "name": repo,
        "added_at": now,
        "active": True,
    }

    # Workspace-scoped config — staged here until workspace creation
    ws_config: dict[str, object] = {}
    if app_type:
        ws_config["app_type"] = app_type
    if analysis is not None:
        ad = asdict(analysis)
        for k, v in ad.items():
            if isinstance(v, tuple):
                ad[k] = list(v)
        ws_config["analysis"] = ad

    if ws_config:
        entry["workspace_config"] = ws_config

    repos.append(entry)
    save_config(cfg)
