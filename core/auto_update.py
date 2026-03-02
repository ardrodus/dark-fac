"""Auto-update system with rollback (US-111).

Version checking via ``git ls-remote --tags``, changelog via ``gh release view``,
git fetch/merge updates, rollback to previous version tag, enable/disable toggle
persisted in config, heartbeat hook for periodic checks in auto mode.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from factory import __version__
from factory.integrations.shell import gh, git

if TYPE_CHECKING:
    from factory.core.config_manager import ConfigData

logger = logging.getLogger(__name__)

_DEFAULT_REPO = "pdistefano/dark-factory"
_CHECK_INTERVAL = 86400  # 24 hours
_SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """Information about an available update."""
    current_version: str
    latest_version: str
    latest_tag: str
    changelog: str
    bump_type: str  # "patch", "minor", or "major"


def _parse_semver(version: str) -> tuple[int, int, int]:
    m = _SEMVER_RE.search(version)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def _version_gt(a: str, b: str) -> bool:
    return _parse_semver(a) > _parse_semver(b)


def _classify_bump(old: str, new: str) -> str:
    o_maj, o_min, _ = _parse_semver(old)
    n_maj, n_min, _ = _parse_semver(new)
    if n_maj != o_maj:
        return "major"
    return "minor" if n_min != o_min else "patch"


# ── Config helpers ────────────────────────────────────────────────

def _load_cfg() -> ConfigData:
    from factory.core.config_manager import load_config
    return load_config()


def _get(cfg: ConfigData | None, key: str, default: object = None) -> object:
    from factory.core.config_manager import get_config_value
    c = cfg or _load_cfg()
    val = get_config_value(c, key)
    return val if val is not None else default


def _set_and_save(key: str, value: object, cfg: ConfigData | None = None) -> None:
    from factory.core.config_manager import save_config, set_config_value
    c = cfg or _load_cfg()
    set_config_value(c, key, value)
    save_config(c)


def is_update_enabled(cfg: ConfigData | None = None) -> bool:
    """Return True if auto-update checks are enabled."""
    return _get(cfg, "auto_update.enabled", True) is True


def set_update_enabled(enabled: bool, cfg: ConfigData | None = None) -> None:
    """Persist the enable/disable toggle for auto-update checks."""
    _set_and_save("auto_update.enabled", enabled, cfg)
    logger.info("Auto-update checks %s", "enabled" if enabled else "disabled")


# ── Version checking ─────────────────────────────────────────────

def _fetch_remote_tags(*, repo: str | None = None, cwd: str | None = None) -> list[str]:
    """Fetch release tags from the remote via ``git ls-remote --tags``."""
    remote = f"https://github.com/{repo or _DEFAULT_REPO}.git"
    result = git(["ls-remote", "--tags", remote], cwd=cwd, timeout=30)
    if result.returncode != 0:
        logger.warning("git ls-remote failed (rc=%d): %s", result.returncode, result.stderr.strip())
        return []
    tags: list[str] = []
    for line in result.stdout.splitlines():
        m = re.search(r"refs/tags/(v\d+\.\d+\.\d+)$", line)
        if m:
            tags.append(m.group(1))
    tags.sort(key=_parse_semver)
    return tags


def get_changelog(tag: str, *, repo: str | None = None) -> str:
    """Fetch release notes for *tag* via ``gh release view``."""
    result = gh(
        ["release", "view", tag, "--repo", repo or _DEFAULT_REPO, "--json", "body", "-q", ".body"],
        timeout=15,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "(No release notes available)"


def check_for_update(
    *, repo: str | None = None, cwd: str | None = None,
) -> UpdateInfo | None:
    """Compare local version against remote; return UpdateInfo or None."""
    current = __version__
    tags = _fetch_remote_tags(repo=repo, cwd=cwd)
    if not tags:
        return None
    latest_tag = tags[-1]
    latest_ver = latest_tag.lstrip("v")
    if not _version_gt(latest_ver, current):
        logger.info("No update available (v%s is current)", current)
        return None
    changelog = get_changelog(latest_tag, repo=repo)
    bump = _classify_bump(current, latest_ver)
    logger.info("Update available: v%s -> %s (%s)", current, latest_tag, bump)
    return UpdateInfo(current_version=current, latest_version=latest_ver,
                      latest_tag=latest_tag, changelog=changelog, bump_type=bump)


# ── Apply update ─────────────────────────────────────────────────

def apply_update(tag: str, *, cwd: str | None = None) -> bool:
    """Apply an update by fetching and merging *tag* via git."""
    _set_and_save("auto_update.previous_version", __version__)
    fetch = git(["fetch", "origin", "--tags"], cwd=cwd, timeout=120)
    if fetch.returncode != 0:
        logger.error("git fetch failed: %s", fetch.stderr.strip())
        return False
    merge = git(["merge", tag, "--ff-only"], cwd=cwd, timeout=60)
    if merge.returncode != 0:
        logger.error("git merge %s failed: %s", tag, merge.stderr.strip())
        return False
    _set_and_save("auto_update.last_update_time", int(time.time()))
    logger.info("Update to %s applied successfully", tag)
    return True


# ── Rollback ─────────────────────────────────────────────────────

def rollback_update(*, cwd: str | None = None) -> bool:
    """Revert to the previous version tag recorded before the last update."""
    cfg = _load_cfg()
    prev = _get(cfg, "auto_update.previous_version")
    if not prev or not isinstance(prev, str):
        logger.error("No previous version recorded -- cannot rollback")
        return False
    prev_tag = prev if prev.startswith("v") else f"v{prev}"
    logger.info("Rolling back to %s", prev_tag)
    git(["fetch", "origin", "--tags"], cwd=cwd, timeout=120)
    checkout = git(["checkout", prev_tag], cwd=cwd, timeout=30)
    if checkout.returncode != 0:
        logger.error("git checkout %s failed: %s", prev_tag, checkout.stderr.strip())
        return False
    logger.info("Rolled back to %s", prev_tag)
    return True


# ── Heartbeat hook ────────────────────────────────────────────────

def _should_check(cfg: ConfigData | None = None) -> bool:
    if not is_update_enabled(cfg):
        return False
    c = cfg or _load_cfg()
    last = _get(c, "auto_update.last_check_time", 0)
    if not isinstance(last, (int, float)):
        return True
    return (time.time() - float(last)) >= _CHECK_INTERVAL


def _record_check_time(cfg: ConfigData | None = None) -> None:
    _set_and_save("auto_update.last_check_time", int(time.time()), cfg)


def heartbeat_check(
    *, auto_mode: bool = False, repo: str | None = None, cwd: str | None = None,
) -> UpdateInfo | None:
    """Periodic update check hook for auto-mode runs.

    In auto mode, patch releases are applied silently.
    Minor/major releases are logged but not auto-applied.
    In interactive mode, returns UpdateInfo for the caller to prompt.
    """
    if not _should_check():
        return None
    _record_check_time()
    info = check_for_update(repo=repo, cwd=cwd)
    if info is None:
        return None
    if auto_mode and info.bump_type == "patch":
        logger.info("Auto-applying patch update %s", info.latest_tag)
        if apply_update(info.latest_tag, cwd=cwd):
            sys.stderr.write(f"Auto-update: applied {info.latest_tag}\n")
            return info
        logger.warning("Auto-apply of %s failed", info.latest_tag)
    return info


# ── Interactive prompt ────────────────────────────────────────────

def interactive_update_prompt(info: UpdateInfo, *, cwd: str | None = None) -> bool:
    """Display an interactive update prompt. Returns True if update was applied."""
    out = sys.stdout.write
    out("\n  === Update Available ===\n\n")
    out(f"  Current: v{info.current_version}\n")
    out(f"  Latest:  {info.latest_tag} ({info.bump_type} release)\n\n")
    out("  [a] Auto-update now\n  [l] View changelog\n")
    out("  [s] Skip this version\n  [d] Disable update checks\n\n")
    try:
        choice = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if choice == "a":
        return apply_update(info.latest_tag, cwd=cwd)
    if choice == "l":
        out(f"\n  Release Notes for {info.latest_tag}:\n  {info.changelog}\n\n")
        out("  Install this update? [y/N] ")
        try:
            confirm = input("").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if confirm == "y":
            return apply_update(info.latest_tag, cwd=cwd)
        out("  Update skipped.\n")
        return False
    if choice == "d":
        set_update_enabled(False)
        out("  Update checks disabled. Re-enable with: dark-factory update --enable\n")
        return False
    out("  Update skipped.\n")
    return False
