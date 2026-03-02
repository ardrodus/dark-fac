"""Structured JSONL pipeline logging with human-readable console output.

Port of ``logger.sh`` (flog).  Dual output: JSONL file + Python logging.
Daily rotation with 7-day retention.  Logs to ``.dark-factory/logs/``.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

_LOG_DIR_NAME = "logs"
_LOG_PREFIX = "factory"
_RETENTION_DAYS = 7


def _resolve_log_dir(config_dir: Path | None = None) -> Path:
    """Return ``.dark-factory/logs/``, creating it if needed."""
    if config_dir is not None:
        log_dir = config_dir / _LOG_DIR_NAME
    else:
        from factory.core.config_manager import resolve_config_dir  # noqa: PLC0415

        log_dir = resolve_config_dir() / _LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _today_str() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _purge_old_logs(log_dir: Path) -> None:
    """Remove JSONL files older than *_RETENTION_DAYS*."""
    cutoff = time.time() - (_RETENTION_DAYS * 86400)
    for path in log_dir.glob(f"{_LOG_PREFIX}-*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                logger.debug("Purged old log: %s", path.name)
        except OSError:
            pass


class PipelineLogger:
    """Dual-output structured logger: JSONL to disk, human-readable to console."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self._log_dir = _resolve_log_dir(config_dir)
        self._current_date = _today_str()
        self._jsonl_path = self._log_dir / f"{_LOG_PREFIX}-{self._current_date}.jsonl"
        _purge_old_logs(self._log_dir)

    def _rotate_if_needed(self) -> None:
        """Switch to a new daily file when the date rolls over."""
        today = _today_str()
        if today != self._current_date:
            self._current_date = today
            self._jsonl_path = self._log_dir / f"{_LOG_PREFIX}-{today}.jsonl"
            _purge_old_logs(self._log_dir)

    def _write_jsonl(self, record: dict[str, Any]) -> None:
        self._rotate_if_needed()
        try:
            line = json.dumps(record, separators=(",", ":")) + "\n"
            with self._jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            logger.warning("Failed to write JSONL log: %s", exc)

    def log(
        self,
        level: str,
        phase: str,
        message: str,
        *,
        tag: str = "",
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write a structured log entry to JSONL and Python logging."""
        ts = datetime.now(tz=timezone.utc).isoformat()
        record: dict[str, Any] = {
            "timestamp": ts,
            "level": level.upper(),
            "phase": phase,
            "tag": tag,
            "message": message,
        }
        if duration_ms is not None:
            record["duration_ms"] = round(duration_ms, 2)
        if metadata:
            record["metadata"] = metadata
        self._write_jsonl(record)

        _PY_LEVEL_MAP = {"WARN": "WARNING"}
        py_level = getattr(logging, _PY_LEVEL_MAP.get(level.upper(), level.upper()), logging.INFO)
        dur_suffix = f" ({duration_ms:.0f}ms)" if duration_ms is not None else ""
        tag_part = f"/{tag}" if tag else ""
        logger.log(py_level, "[%s%s] %s%s", phase, tag_part, message, dur_suffix)

    def info(self, phase: str, message: str, **kwargs: Any) -> None:
        self.log("INFO", phase, message, **kwargs)

    def warn(self, phase: str, message: str, **kwargs: Any) -> None:
        self.log("WARN", phase, message, **kwargs)

    def error(self, phase: str, message: str, **kwargs: Any) -> None:
        self.log("ERROR", phase, message, **kwargs)

    def debug(self, phase: str, message: str, **kwargs: Any) -> None:
        self.log("DEBUG", phase, message, **kwargs)

    @contextmanager
    def phase(
        self,
        name: str,
        *,
        tag: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[None]:
        """Context manager that auto-tracks phase duration."""
        self.info(name, "Phase started", tag=tag)
        start = time.monotonic()
        try:
            yield
        except BaseException:
            elapsed = (time.monotonic() - start) * 1000
            self.error(name, "Phase failed", tag=tag, duration_ms=elapsed, metadata=metadata)
            raise
        else:
            elapsed = (time.monotonic() - start) * 1000
            self.info(name, "Phase completed", tag=tag, duration_ms=elapsed, metadata=metadata)
