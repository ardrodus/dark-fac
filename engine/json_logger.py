"""Structured JSON line logger for pipeline execution.

Writes one JSON object per line to ``.dark-factory/logs/factory.jsonl``.
Designed for programmatic failure detection by the Obelisk watcher.

Log format::

    {"ts": "2026-03-06T15:30:00.123Z", "level": "INFO", "source": "runner",
     "pipeline": "my-pipeline", "node": "build", "msg": "Stage started"}

Optional fields: ``error``, ``traceback``.

Levels: INFO, WARN, ERROR, FATAL.
Sources: runner, process.

Rotation: files older than 14 days are removed on logger init.
"""

from __future__ import annotations

import json
import time
import traceback as tb_mod
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class LogLevel(StrEnum):
    """Structured log levels."""

    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


class LogSource(StrEnum):
    """Log source identifiers."""

    RUNNER = "runner"
    PROCESS = "process"


_ROTATION_DAYS = 14
_ROTATION_SECONDS = _ROTATION_DAYS * 86400


def _rotate_old_logs(log_dir: Path) -> None:
    """Remove .jsonl files older than the rotation window."""
    cutoff = time.time() - _ROTATION_SECONDS
    try:
        for entry in log_dir.iterdir():
            if entry.suffix == ".jsonl" and entry.name != "factory.jsonl":
                try:
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _rotate_current_log(log_path: Path) -> None:
    """Archive the current log if it is older than the rotation window."""
    try:
        if not log_path.exists():
            return
        mtime = log_path.stat().st_mtime
        if time.time() - mtime >= _ROTATION_SECONDS:
            ts = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y%m%dT%H%M%SZ")
            archive = log_path.with_name(f"factory-{ts}.jsonl")
            log_path.rename(archive)
    except OSError:
        pass


class FactoryJsonLogger:
    """Append-only JSON-lines logger writing to factory.jsonl.

    Usage::

        logger = FactoryJsonLogger(workspace)
        logger.info("runner", "Pipeline started", pipeline="my-pipe")
        logger.error("runner", "Stage failed", pipeline="p", node="n",
                      error="RuntimeError", traceback="...")
    """

    def __init__(self, workspace: Path | str) -> None:
        self._log_dir = Path(workspace) / ".dark-factory" / "logs"
        self._log_path = self._log_dir / "factory.jsonl"
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        _rotate_current_log(self._log_path)
        _rotate_old_logs(self._log_dir)

    @property
    def log_path(self) -> Path:
        """Return the path to the active log file."""
        return self._log_path

    def _write(
        self,
        level: LogLevel,
        source: str,
        msg: str,
        *,
        pipeline: str = "",
        node: str = "",
        error: str = "",
        traceback: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write a single JSON log line."""
        now = datetime.now(tz=UTC)
        record: dict[str, Any] = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            "level": str(level),
            "source": source,
            "pipeline": pipeline,
            "node": node,
            "msg": msg,
        }
        if error:
            record["error"] = error
        if traceback:
            record["traceback"] = traceback
        if extra:
            record.update(extra)
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def info(
        self,
        source: str,
        msg: str,
        **kwargs: Any,
    ) -> None:
        """Log an INFO-level message."""
        self._write(LogLevel.INFO, source, msg, **kwargs)

    def warn(
        self,
        source: str,
        msg: str,
        **kwargs: Any,
    ) -> None:
        """Log a WARN-level message."""
        self._write(LogLevel.WARN, source, msg, **kwargs)

    def error(
        self,
        source: str,
        msg: str,
        **kwargs: Any,
    ) -> None:
        """Log an ERROR-level message."""
        self._write(LogLevel.ERROR, source, msg, **kwargs)

    def fatal(
        self,
        source: str,
        msg: str,
        **kwargs: Any,
    ) -> None:
        """Log a FATAL-level message."""
        self._write(LogLevel.FATAL, source, msg, **kwargs)

    def log_exception(
        self,
        source: str,
        msg: str,
        exc: BaseException,
        *,
        pipeline: str = "",
        node: str = "",
    ) -> None:
        """Log an ERROR with exception details and traceback."""
        self._write(
            LogLevel.ERROR,
            source,
            msg,
            pipeline=pipeline,
            node=node,
            error=f"{type(exc).__name__}: {exc}",
            traceback="".join(tb_mod.format_exception(exc)),
        )
