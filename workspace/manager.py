"""Workspace management — create, cache, clean, and list workspaces.

All workspace operations route through this single module.  Workspaces
are isolated git worktrees or clones used by agents during issue
processing.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dark_factory.integrations.shell import CommandError, git

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE_ROOT = Path(".dark-factory") / "workspaces"
_CACHE_FILENAME = "workspace_cache.json"
_DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days

# Security-relevant file basenames (exact match).
_SECURITY_BASENAMES: frozenset[str] = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Gemfile", "Gemfile.lock", "Pipfile", "Pipfile.lock", "poetry.lock",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
    "composer.json", "composer.lock", ".env.example",
    "docker-compose.yml", "docker-compose.yaml",
})

# Security-relevant file prefixes (startswith match).
_SECURITY_PREFIXES: tuple[str, ...] = ("Dockerfile", "docker-compose", "requirements")


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
class Workspace:
    """Rich workspace handle returned by :func:`acquire_workspace`."""

    name: str
    path: str
    repo_url: str
    branch: str
    created_at: float = field(default_factory=time.time)
    security_files_changed: bool = False
    sentinel_passed: bool = True


@dataclass(frozen=True, slots=True)
class WorkspaceResult:
    """Result of a workspace operation."""

    success: bool
    workspace: WorkspaceInfo | None = None
    message: str = ""


# ── Cache helpers ─────────────────────────────────────────────────


def _resolve_root(root: Path | None) -> Path:
    """Return the workspace root directory as an absolute path, creating it if needed."""
    directory = (root or _DEFAULT_WORKSPACE_ROOT).resolve()
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
        _force_rmtree(ws_path)

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


def acquire_workspace(
    repo: str,
    issue: int,
    *,
    root: Path | None = None,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> Workspace:
    """Acquire a workspace: clone-or-pull, branch, security detection, Sentinel.

    Parameters
    ----------
    repo:
        Repository identifier, e.g. ``"owner/repo"``.
    issue:
        Issue number used for branch naming (``dark-factory/issue-<N>``).
    root:
        Override for the workspace root directory.
    ttl_seconds:
        Maximum age in seconds; stale workspaces are cleaned first.

    Returns
    -------
    Workspace:
        A rich handle with security metadata.

    Raises
    ------
    RuntimeError:
        If Sentinel gates fail on security-relevant changes.
    """
    resolved = _resolve_root(root)
    _clean_stale_workspaces(resolved, ttl_seconds)

    owner, repo_name = _parse_repo_key(repo)
    ws_name = f"{owner}/{repo_name}"
    ws_path = resolved / owner / repo_name
    branch = f"dark-factory/issue-{issue}"
    repo_url = _build_clone_url(repo)

    existing = get_workspace(ws_name, root=resolved)
    security_changed = False

    if existing is not None and (ws_path / ".git").is_dir() and _is_clean(ws_path):
        # Smart-pull: existing clean workspace → pull latest
        default_branch = _detect_default_branch(ws_path)
        security_changed = _smart_pull(ws_path, default_branch)
    else:
        # Fresh clone (stale/dirty workspace removed first)
        if ws_path.exists():
            _force_rmtree(ws_path)
        _clone_fresh(ws_path, repo_url)

    # Create issue-specific branch (idempotent)
    _ensure_branch(ws_path, branch)

    # If security-relevant files changed, trigger Sentinel
    sentinel_passed = True
    if security_changed:
        sentinel_passed = _run_sentinel_gate(ws_path)
        if not sentinel_passed:
            logger.error("Sentinel failed for %s — ejecting workspace", ws_name)
            if ws_path.exists():
                _force_rmtree(ws_path)
            _remove_from_cache(ws_name, resolved)
            msg = f"Sentinel security gate failed for {repo} (issue #{issue})"
            raise RuntimeError(msg)

    # Upsert cache entry
    info = WorkspaceInfo(
        name=ws_name, path=str(ws_path), repo_url=repo_url, branch=branch,
    )
    cache_workspace(info, root=resolved)

    logger.info("Acquired workspace %s at %s (branch=%s)", ws_name, ws_path, branch)
    return Workspace(
        name=ws_name, path=str(ws_path), repo_url=repo_url,
        branch=branch, security_files_changed=security_changed,
        sentinel_passed=sentinel_passed,
    )


# ── Acquisition helpers (private) ─────────────────────────────────


def _parse_repo_key(repo: str) -> tuple[str, str]:
    """Split ``"owner/repo"`` into ``(owner, repo_name)``."""
    parts = repo.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:  # noqa: PLR2004
        msg = f"Invalid repo key {repo!r} — expected 'owner/repo'"
        raise ValueError(msg)
    return parts[0], parts[1]


def _build_clone_url(repo: str) -> str:
    """Build HTTPS clone URL, using ``GH_TOKEN`` when available."""
    token = os.environ.get("GH_TOKEN", "")
    if token:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


def _is_clean(ws_path: Path) -> bool:
    """Return ``True`` if the workspace has no uncommitted changes."""
    result = git(["status", "--porcelain"], cwd=str(ws_path))
    return result.returncode == 0 and not result.stdout.strip()


def _detect_default_branch(ws_path: Path) -> str:
    """Auto-detect default branch (main vs master) for the workspace."""
    cwd = str(ws_path)
    # Try symbolic-ref of origin/HEAD first
    result = git(["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], cwd=cwd)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().removeprefix("origin/")
    # Fall back: check if 'main' branch exists, else 'master'
    for candidate in ("main", "master"):
        check = git(["rev-parse", "--verify", f"refs/heads/{candidate}"], cwd=cwd)
        if check.returncode == 0:
            return candidate
    return "main"


def _smart_pull(ws_path: Path, default_branch: str) -> bool:
    """Pull latest on *default_branch*. Returns ``True`` if security files changed."""
    cwd = str(ws_path)
    before = git(["rev-parse", "HEAD"], cwd=cwd).stdout.strip()

    git(["checkout", default_branch], cwd=cwd, check=True)
    git(["pull", "origin", default_branch], cwd=cwd, check=True, timeout=120)

    after = git(["rev-parse", "HEAD"], cwd=cwd).stdout.strip()
    if before == after:
        return False

    diff_result = git(["diff", "--name-only", before, after], cwd=cwd)
    changed_files = [f for f in diff_result.stdout.splitlines() if f.strip()]
    return _has_security_relevant_files(changed_files, ws_path=ws_path, old_ref=before)


def _has_security_relevant_files(
    changed_files: list[str],
    *,
    ws_path: Path | None = None,
    old_ref: str | None = None,
) -> bool:
    """Return ``True`` if any changed file is security-relevant."""
    for filepath in changed_files:
        basename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
        if basename in _SECURITY_BASENAMES:
            logger.info("Security-relevant file changed: %s", filepath)
            return True
        for prefix in _SECURITY_PREFIXES:
            if basename.startswith(prefix):
                logger.info("Security-relevant file changed: %s (prefix %s)", filepath, prefix)
                return True
        # Files inside .github/workflows/ are always security-relevant
        if filepath.startswith(".github/workflows/"):
            logger.info("Security-relevant file changed: %s (workflow)", filepath)
            return True
        # Files in a directory that didn't exist before the pull
        if ws_path and old_ref and "/" in filepath:
            parent_dir = filepath.rsplit("/", 1)[0]
            check = git(["show", f"{old_ref}:{parent_dir}/"], cwd=str(ws_path))
            if check.returncode != 0:
                logger.info("File in new directory: %s (dir: %s)", filepath, parent_dir)
                return True
    return False


def _force_rmtree(path: Path) -> None:
    """Remove a directory tree, handling Windows file-lock errors on .git pack files."""
    import subprocess  # noqa: PLC0415

    # On Windows, .git pack files are often locked. Use OS-level removal.
    if os.name == "nt":
        # Try PowerShell Remove-Item first (handles locks better than Python)
        try:
            subprocess.run(
                ["powershell", "-Command", f"Remove-Item -Recurse -Force '{path}'"],
                timeout=30, check=False, capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not path.exists():
            return

    # Fallback: Python rmtree with chmod retry
    def _onerror(_fn: object, fpath: str, _exc_info: object) -> None:
        try:
            os.chmod(fpath, 0o777)
            os.unlink(fpath)
        except OSError:
            pass

    shutil.rmtree(path, onerror=_onerror)


def _clone_fresh(ws_path: Path, repo_url: str) -> None:
    """Clone a repo, auto-detecting the default branch."""
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    # Clone without --branch to get whatever the remote default is
    git(["clone", repo_url, str(ws_path)], check=True, timeout=120)


def _ensure_branch(ws_path: Path, branch: str) -> None:
    """Checkout *branch*, creating it if it doesn't exist."""
    cwd = str(ws_path)
    result = git(["checkout", branch], cwd=cwd)
    if result.returncode != 0:
        git(["checkout", "-b", branch], cwd=cwd, check=True)


def _run_sentinel_gate(ws_path: Path) -> bool:
    """Run Sentinel security scans on the workspace.

    Imports :class:`~factory.gates.framework.GateRunner` lazily to avoid
    circular dependencies.
    """
    if os.environ.get("WSREG_SKIP_SENTINEL", "").lower() == "true":
        logger.info("Sentinel gates skipped (WSREG_SKIP_SENTINEL=true)")
        return True

    from dark_factory.gates.framework import GateRunner  # noqa: PLC0415

    runner = GateRunner("sentinel-acquire", metrics_dir=str(ws_path.parent))
    cwd = str(ws_path)

    def _check_secrets() -> bool:
        r = git(["log", "--oneline", "-1"], cwd=cwd)
        return r.returncode == 0

    def _check_dependencies() -> bool:
        for lockfile in ("package-lock.json", "yarn.lock", "requirements.txt"):
            if (ws_path / lockfile).exists():
                logger.debug("Dependency file present: %s", lockfile)
        return True

    runner.register_check("secret-scan", _check_secrets)
    runner.register_check("dependency-scan", _check_dependencies)

    report = runner.run()
    return report.passed


def _clean_stale_workspaces(root: Path, ttl_seconds: float) -> int:
    """Remove workspaces older than *ttl_seconds*. Returns count removed."""
    now = time.time()
    removed = 0
    for ws in list_workspaces(root=root):
        if ws.created_at > 0 and (now - ws.created_at) > ttl_seconds:
            logger.info("TTL expired for workspace %s (age=%.0fs)", ws.name, now - ws.created_at)
            clean_workspace(ws.name, root=root)
            removed += 1
    return removed


def _remove_from_cache(name: str, root: Path) -> None:
    """Remove a single entry from the workspace cache."""
    cache = _load_cache(root)
    cache.pop(name, None)
    _save_cache(root, cache)


# ── Git helpers (private) ─────────────────────────────────────────


def _clone_repo(dest: Path, repo_url: str, branch: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
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
