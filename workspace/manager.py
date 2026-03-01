"""Workspace management — create, cache, clean, and list workspaces.

All workspace operations route through this single module.  Workspaces
are isolated git worktrees or clones used by agents during issue
processing.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from factory.integrations.shell import CommandError, git

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE_ROOT = Path(".dark-factory") / "workspaces"
_CACHE_FILENAME = "workspace_cache.json"


# ── Value objects ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkspaceInfo:
    """Immutable snapshot of a single workspace."""

    name: str
    path: str
    repo_url: str
    branch: str
    created_at: float = field(default_factory=time.time)
    is_worktree: bool = False


@dataclass(frozen=True, slots=True)
class WorkspaceResult:
    """Result of a workspace operation."""

    success: bool
    workspace: WorkspaceInfo | None = None
    message: str = ""


# ── Cache helpers ─────────────────────────────────────────────────


def _resolve_root(root: Path | None) -> Path:
    """Return the workspace root directory, creating it if needed."""
    directory = root or _DEFAULT_WORKSPACE_ROOT
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_path(root: Path) -> Path:
    return root / _CACHE_FILENAME


def _load_cache(root: Path) -> dict[str, object]:
    """Load the workspace cache from disk."""
    path = _cache_path(root)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data: object = json.load(fh)
    return data if isinstance(data, dict) else {}


def _save_cache(root: Path, cache: dict[str, object]) -> Path:
    """Persist the workspace cache to disk and return the file path."""
    path = _cache_path(root)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2)
    return path


def _info_to_dict(info: WorkspaceInfo) -> dict[str, object]:
    return asdict(info)


def _dict_to_info(data: dict[str, object]) -> WorkspaceInfo:
    """Reconstruct a :class:`WorkspaceInfo` from a raw dict."""
    return WorkspaceInfo(
        name=str(data.get("name", "")),
        path=str(data.get("path", "")),
        repo_url=str(data.get("repo_url", "")),
        branch=str(data.get("branch", "")),
        created_at=float(raw_ts) if isinstance(raw_ts := data.get("created_at"), (int, float)) else 0.0,
        is_worktree=bool(data.get("is_worktree", False)),
    )


# ── Public API ────────────────────────────────────────────────────


def cache_workspace(
    info: WorkspaceInfo,
    *,
    root: Path | None = None,
) -> Path:
    """Write *info* to the workspace cache.  Returns the cache file path."""
    resolved = _resolve_root(root)
    cache = _load_cache(resolved)
    cache[info.name] = _info_to_dict(info)
    return _save_cache(resolved, cache)


def list_workspaces(*, root: Path | None = None) -> list[WorkspaceInfo]:
    """Return all cached workspace entries."""
    resolved = _resolve_root(root)
    cache = _load_cache(resolved)
    results: list[WorkspaceInfo] = []
    for value in cache.values():
        if isinstance(value, dict):
            results.append(_dict_to_info(value))
    return results


def get_workspace(name: str, *, root: Path | None = None) -> WorkspaceInfo | None:
    """Look up a single workspace by *name*.  Returns ``None`` if not found."""
    resolved = _resolve_root(root)
    cache = _load_cache(resolved)
    raw = cache.get(name)
    if isinstance(raw, dict):
        return _dict_to_info(raw)
    return None


def create_workspace(
    name: str,
    repo_url: str,
    *,
    branch: str = "main",
    root: Path | None = None,
    use_worktree: bool = False,
    worktree_base: str | None = None,
) -> WorkspaceResult:
    """Create a new workspace via ``git clone`` or ``git worktree add``.

    When *use_worktree* is ``True``, *worktree_base* must point to an
    existing local repository that will serve as the worktree parent.
    """
    resolved = _resolve_root(root)
    ws_path = resolved / name

    if ws_path.exists():
        existing = get_workspace(name, root=resolved)
        return WorkspaceResult(success=False, workspace=existing, message=f"Workspace '{name}' already exists")

    try:
        if use_worktree:
            _create_worktree(ws_path, branch, worktree_base)
        else:
            _clone_repo(ws_path, repo_url, branch)
    except CommandError as exc:
        return WorkspaceResult(success=False, message=f"git failed: {exc}")

    info = WorkspaceInfo(
        name=name,
        path=str(ws_path),
        repo_url=repo_url,
        branch=branch,
        is_worktree=use_worktree,
    )
    cache_workspace(info, root=resolved)
    logger.info("Created workspace '%s' at %s", name, ws_path)
    return WorkspaceResult(success=True, workspace=info)


def clean_workspace(name: str, *, root: Path | None = None) -> WorkspaceResult:
    """Remove a single workspace by *name* and clear its cache entry."""
    resolved = _resolve_root(root)
    info = get_workspace(name, root=resolved)

    if info is None:
        return WorkspaceResult(success=False, message=f"Workspace '{name}' not found")

    ws_path = Path(info.path)
    if info.is_worktree:
        _remove_worktree(ws_path)
    elif ws_path.exists():
        shutil.rmtree(ws_path)

    cache = _load_cache(resolved)
    cache.pop(name, None)
    _save_cache(resolved, cache)
    logger.info("Cleaned workspace '%s'", name)
    return WorkspaceResult(success=True, workspace=info, message="removed")


def clean_all_workspaces(*, root: Path | None = None) -> int:
    """Remove every cached workspace.  Returns the number removed."""
    resolved = _resolve_root(root)
    workspaces = list_workspaces(root=resolved)
    count = 0
    for ws in workspaces:
        result = clean_workspace(ws.name, root=resolved)
        if result.success:
            count += 1
    return count


# ── Git helpers (private) ─────────────────────────────────────────


def _clone_repo(dest: Path, repo_url: str, branch: str) -> None:
    git(["clone", "--branch", branch, "--single-branch", repo_url, str(dest)], check=True, timeout=120)


def _create_worktree(dest: Path, branch: str, base: str | None) -> None:
    cwd = base or "."
    git(["worktree", "add", str(dest), "-b", branch], check=True, cwd=cwd)


def _remove_worktree(ws_path: Path) -> None:
    try:
        git(["worktree", "remove", "--force", str(ws_path)], check=True)
    except CommandError:
        logger.warning("git worktree remove failed for %s; falling back to rmtree", ws_path)
        if ws_path.exists():
            shutil.rmtree(ws_path)
