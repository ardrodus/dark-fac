"""PID-based instance lock to prevent concurrent factory runs."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)
_LOCK_FILENAME = "dark_factory.lock"


class InstanceLockError(Exception):
    """Raised when the instance lock cannot be acquired."""


def _pid_alive(pid: int) -> bool:
    """Return ``True`` if *pid* refers to a running process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes  # noqa: PLC0415
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[union-attr]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[union-attr]
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we lack permission
    return True


def _resolve_lock_path(config_dir: Path | None = None) -> Path:
    """Return the path to the lock file inside the config directory."""
    if config_dir is not None:
        return config_dir / _LOCK_FILENAME
    from dark_factory.core.config_manager import resolve_config_dir  # noqa: PLC0415
    return resolve_config_dir() / _LOCK_FILENAME


def acquire_lock(config_dir: Path | None = None) -> Path:
    """Create ``.dark-factory/factory.lock`` with current PID; reclaim stale locks."""
    lock_path = _resolve_lock_path(config_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    my_pid = os.getpid()
    if lock_path.is_file():
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            existing_pid = -1
        if _pid_alive(existing_pid):
            raise InstanceLockError(
                f"Another Dark Factory instance is already running (PID {existing_pid}).\n"
                f"Lock file: {lock_path}\n"
                "If this is incorrect, remove the lock file and retry."
            )
        logger.warning("Cleaned stale lock (PID %d no longer running)", existing_pid)
        lock_path.unlink(missing_ok=True)
    lock_path.write_text(str(my_pid) + "\n", encoding="utf-8")
    logger.info("Instance lock acquired (PID %d)", my_pid)
    return lock_path


def release_lock(config_dir: Path | None = None) -> None:
    """Release the instance lock if held by this process."""
    lock_path = _resolve_lock_path(config_dir)
    if not lock_path.is_file():
        return
    try:
        lock_pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        lock_pid = -1
    if lock_pid == os.getpid():
        lock_path.unlink(missing_ok=True)
        logger.info("Instance lock released (PID %d)", lock_pid)


@contextmanager
def instance_lock(config_dir: Path | None = None) -> Iterator[Path]:
    """Context manager: ``with instance_lock(): ...``"""
    lock_path = acquire_lock(config_dir)
    try:
        yield lock_path
    finally:
        release_lock(config_dir)
