"""App-type selection and config initialization — port of init_config() flow."""
from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dark_factory.setup.project_analyzer import AnalysisResult

_CONFIG_VERSION = 1

_APP_TYPE_MENU = (
    ("1", "console", "Console     CLI tool, no server deployment"),
    ("2", "web", "Web         Web app with Docker, CI/CD"),
)


def prompt_app_type(
    analysis: AnalysisResult | None = None,
) -> str:
    """Present app type choices based on analysis signals.

    Returns the selected app type name (``console`` or ``web``).
    """
    w = sys.stdout.write
    w("\n  Select app type:\n\n")
    for num, _, label in _APP_TYPE_MENU:
        w(f"    [{num}] {label}\n")
    if analysis and analysis.detected_app_type != "console":
        w(
            f"\n  Detected: {analysis.detected_app_type}"
            f" (confidence: {analysis.confidence})\n"
        )
    w("\n")
    try:
        choice = input("  Choice [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        choice = "1"
    app_type = next((s for n, s, _ in _APP_TYPE_MENU if choice == n), "console")
    w(f"  + App type: {app_type}\n")
    return app_type


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
        "analysis": {},
        "app_type": "",
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
    If *analysis* is provided, its fields are merged into the repo entry and
    also stored in the top-level ``analysis`` and ``app_type`` keys.
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
