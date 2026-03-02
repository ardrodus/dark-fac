"""Environment context for agent system prompts.

Replaces attractor_agent.env_context with a lightweight implementation
that builds environment context blocks for the agent's system prompt.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any


def get_git_context(working_dir: str | None = None) -> dict[str, Any]:
    """Gather git repository context for the working directory.

    Returns a dict with keys like 'git_root', 'branch', etc.
    Non-fatal: returns empty dict if not in a git repo.
    """
    import subprocess  # noqa: PLC0415

    cwd = working_dir or os.getcwd()
    result: dict[str, Any] = {}
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if root.returncode == 0:
            result["git_root"] = root.stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if branch.returncode == 0:
            result["branch"] = branch.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return result


def build_environment_context(
    *,
    working_dir: str | None = None,
    model: str = "",
    git_info: dict[str, Any] | None = None,
) -> str:
    """Build an <environment> XML block for the agent's system prompt."""
    cwd = working_dir or os.getcwd()
    info = git_info or {}
    parts = [
        "<environment>",
        f"  <working_directory>{cwd}</working_directory>",
        f"  <platform>{platform.system()}</platform>",
    ]
    if model:
        parts.append(f"  <model>{model}</model>")
    git_root = info.get("git_root")
    if git_root:
        parts.append(f"  <git_root>{git_root}</git_root>")
    branch = info.get("branch")
    if branch:
        parts.append(f"  <git_branch>{branch}</git_branch>")
    cwd_path = Path(cwd)
    if cwd_path.is_dir():
        parts.append(f"  <is_git_repo>{bool(git_root)}</is_git_repo>")
    parts.append("</environment>")
    return "\n".join(parts)
