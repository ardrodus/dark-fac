"""Tests for dark_factory.engine.json_logger — structured JSON line logger."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from dark_factory.engine.json_logger import (
    _ROTATION_SECONDS,
    FactoryJsonLogger,
    LogLevel,
    LogSource,
    _rotate_current_log,
    _rotate_old_logs,
)

# ── Helpers ──────────────────────────────────────────────────────


def _read_lines(path: Path) -> list[dict]:
    """Read all JSON lines from a file."""
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


# ── Basic logging ────────────────────────────────────────────────


class TestFactoryJsonLogger:
    def test_creates_log_dir(self, tmp_path: Path) -> None:
        ws = tmp_path / "project"
        FactoryJsonLogger(ws)
        assert (ws / ".dark-factory" / "logs").is_dir()

    def test_info_writes_json_line(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "Pipeline started", pipeline="test-pipe")

        records = _read_lines(logger.log_path)
        assert len(records) == 1
        r = records[0]
        assert r["level"] == "INFO"
        assert r["source"] == "runner"
        assert r["msg"] == "Pipeline started"
        assert r["pipeline"] == "test-pipe"
        assert "ts" in r

    def test_warn_level(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.warn("runner", "Retrying", pipeline="p", node="n")

        records = _read_lines(logger.log_path)
        assert records[0]["level"] == "WARN"
        assert records[0]["node"] == "n"

    def test_error_with_error_field(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.error("process", "Handler failed", error="RuntimeError: boom")

        records = _read_lines(logger.log_path)
        assert records[0]["level"] == "ERROR"
        assert records[0]["error"] == "RuntimeError: boom"

    def test_fatal_level(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.fatal("runner", "Pipeline aborted")

        records = _read_lines(logger.log_path)
        assert records[0]["level"] == "FATAL"

    def test_optional_fields_omitted_when_empty(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "Hello")

        records = _read_lines(logger.log_path)
        r = records[0]
        assert "error" not in r
        assert "traceback" not in r

    def test_extra_fields_merged(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "Done", extra={"duration_s": 1.5, "artifacts": 3})

        records = _read_lines(logger.log_path)
        r = records[0]
        assert r["duration_s"] == 1.5
        assert r["artifacts"] == 3

    def test_multiple_lines_appended(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "First")
        logger.info("runner", "Second")
        logger.error("process", "Third")

        records = _read_lines(logger.log_path)
        assert len(records) == 3
        assert records[0]["msg"] == "First"
        assert records[2]["level"] == "ERROR"

    def test_log_exception(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        try:
            raise ValueError("test error")
        except ValueError as exc:
            logger.log_exception(
                "process", "Handler crashed", exc, pipeline="p", node="build"
            )

        records = _read_lines(logger.log_path)
        r = records[0]
        assert r["level"] == "ERROR"
        assert r["error"] == "ValueError: test error"
        assert "traceback" in r
        assert "ValueError" in r["traceback"]

    def test_log_path_property(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        expected = tmp_path / ".dark-factory" / "logs" / "factory.jsonl"
        assert logger.log_path == expected


# ── Log format ───────────────────────────────────────────────────


class TestLogFormat:
    def test_required_fields_present(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "test", pipeline="p", node="n")

        records = _read_lines(logger.log_path)
        r = records[0]
        required = {"ts", "level", "source", "pipeline", "node", "msg"}
        assert required.issubset(r.keys())

    def test_timestamp_format(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "test")

        records = _read_lines(logger.log_path)
        ts = records[0]["ts"]
        # Format: 2026-03-06T15:30:00.123Z
        assert ts.endswith("Z")
        assert "T" in ts
        assert "." in ts


# ── Enums ────────────────────────────────────────────────────────


class TestEnums:
    def test_log_levels(self) -> None:
        assert set(LogLevel) == {"INFO", "WARN", "ERROR", "FATAL"}

    def test_log_sources(self) -> None:
        assert set(LogSource) == {"runner", "process"}


# ── Rotation ─────────────────────────────────────────────────────


class TestRotation:
    def test_rotate_old_logs_removes_stale_files(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        old_file = log_dir / "factory-20260101T000000Z.jsonl"
        old_file.write_text("{}\n", encoding="utf-8")
        # Set mtime to 30 days ago
        old_mtime = time.time() - 30 * 86400
        os.utime(old_file, (old_mtime, old_mtime))

        recent_file = log_dir / "factory-20260305T000000Z.jsonl"
        recent_file.write_text("{}\n", encoding="utf-8")

        _rotate_old_logs(log_dir)

        assert not old_file.exists()
        assert recent_file.exists()

    def test_rotate_old_logs_preserves_active_log(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        active = log_dir / "factory.jsonl"
        active.write_text("{}\n", encoding="utf-8")
        old_mtime = time.time() - 30 * 86400
        os.utime(active, (old_mtime, old_mtime))

        _rotate_old_logs(log_dir)
        assert active.exists()

    def test_rotate_current_log_archives_old(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        active = log_dir / "factory.jsonl"
        active.write_text("{}\n", encoding="utf-8")
        old_mtime = time.time() - 30 * 86400
        os.utime(active, (old_mtime, old_mtime))

        _rotate_current_log(active)

        assert not active.exists()
        archived = list(log_dir.glob("factory-*.jsonl"))
        assert len(archived) == 1

    def test_rotate_current_log_keeps_recent(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        active = log_dir / "factory.jsonl"
        active.write_text("{}\n", encoding="utf-8")

        _rotate_current_log(active)
        assert active.exists()

    def test_init_triggers_rotation(self, tmp_path: Path) -> None:
        """FactoryJsonLogger.__init__ rotates old archive files."""
        log_dir = tmp_path / ".dark-factory" / "logs"
        log_dir.mkdir(parents=True)

        old_archive = log_dir / "factory-20251201T000000Z.jsonl"
        old_archive.write_text("{}\n", encoding="utf-8")
        old_mtime = time.time() - 30 * 86400
        os.utime(old_archive, (old_mtime, old_mtime))

        FactoryJsonLogger(tmp_path)
        assert not old_archive.exists()

    def test_rotation_boundary_just_over_14_days(self, tmp_path: Path) -> None:
        """A file exactly 1 second past the 14-day window is removed."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        boundary_file = log_dir / "factory-boundary.jsonl"
        boundary_file.write_text("{}\n", encoding="utf-8")
        mtime = time.time() - _ROTATION_SECONDS - 1
        os.utime(boundary_file, (mtime, mtime))

        _rotate_old_logs(log_dir)
        assert not boundary_file.exists()

    def test_rotation_boundary_just_under_14_days(self, tmp_path: Path) -> None:
        """A file 1 second inside the 14-day window is preserved."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        boundary_file = log_dir / "factory-boundary.jsonl"
        boundary_file.write_text("{}\n", encoding="utf-8")
        mtime = time.time() - _ROTATION_SECONDS + 60  # 1 min inside window
        os.utime(boundary_file, (mtime, mtime))

        _rotate_old_logs(log_dir)
        assert boundary_file.exists()


# ── JSON schema validation (US-006) ──────────────────────────────


_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)

_BASE_SCHEMA = {
    "ts": str,
    "level": str,
    "source": str,
    "pipeline": str,
    "node": str,
    "msg": str,
}


class TestJsonSchema:
    """Verify every log entry matches the expected JSON schema."""

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "First")
        logger.error("process", "Second", error="Err")
        logger.fatal("runner", "Third")

        raw = logger.log_path.read_text(encoding="utf-8").strip().splitlines()
        for line in raw:
            parsed = json.loads(line)  # raises on invalid JSON
            assert isinstance(parsed, dict)

    def test_base_fields_present_and_typed(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "Hello", pipeline="p", node="n")

        records = _read_lines(logger.log_path)
        r = records[0]
        for field, expected_type in _BASE_SCHEMA.items():
            assert field in r, f"Missing field: {field}"
            assert isinstance(r[field], expected_type), (
                f"Field {field} should be {expected_type.__name__}, "
                f"got {type(r[field]).__name__}"
            )

    def test_timestamp_matches_iso8601_millis(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "ts check")

        records = _read_lines(logger.log_path)
        assert _TIMESTAMP_RE.match(records[0]["ts"]), (
            f"Timestamp {records[0]['ts']!r} does not match YYYY-MM-DDTHH:MM:SS.mmmZ"
        )

    def test_level_is_valid_enum_value(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        for method in (logger.info, logger.warn, logger.error, logger.fatal):
            method("runner", "test")

        records = _read_lines(logger.log_path)
        valid_levels = {str(lv) for lv in LogLevel}
        for r in records:
            assert r["level"] in valid_levels

    def test_no_unexpected_base_fields(self, tmp_path: Path) -> None:
        """A plain INFO entry has exactly the base fields, no extras."""
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "plain", pipeline="p", node="n")

        records = _read_lines(logger.log_path)
        assert set(records[0].keys()) == set(_BASE_SCHEMA.keys())

    def test_error_entry_schema_includes_error_field(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.error("runner", "fail", error="RuntimeError: boom")

        records = _read_lines(logger.log_path)
        r = records[0]
        assert "error" in r
        assert isinstance(r["error"], str)

    def test_extra_fields_do_not_remove_base_fields(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.info("runner", "with extras", extra={"custom": 42})

        records = _read_lines(logger.log_path)
        r = records[0]
        for field in _BASE_SCHEMA:
            assert field in r


# ── ERROR / FATAL required fields (US-006) ───────────────────────


class TestErrorFatalRequiredFields:
    """ERROR and FATAL entries include all required fields."""

    def test_error_has_all_base_fields(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.error("process", "Stage failed", pipeline="build", node="compile")

        records = _read_lines(logger.log_path)
        r = records[0]
        for field in _BASE_SCHEMA:
            assert field in r
        assert r["level"] == "ERROR"

    def test_fatal_has_all_base_fields(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.fatal("runner", "Pipeline aborted", pipeline="deploy", node="push")

        records = _read_lines(logger.log_path)
        r = records[0]
        for field in _BASE_SCHEMA:
            assert field in r
        assert r["level"] == "FATAL"

    def test_error_with_exception_includes_error_and_traceback(
        self, tmp_path: Path
    ) -> None:
        logger = FactoryJsonLogger(tmp_path)
        try:
            raise RuntimeError("process crashed")
        except RuntimeError as exc:
            logger.log_exception("runner", "Unhandled", exc, pipeline="p", node="n")

        records = _read_lines(logger.log_path)
        r = records[0]
        assert r["level"] == "ERROR"
        assert "error" in r
        assert "RuntimeError" in r["error"]
        assert "traceback" in r
        assert len(r["traceback"]) > 0

    def test_fatal_with_error_field(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.fatal(
            "runner",
            "Unrecoverable",
            pipeline="p",
            node="n",
            error="OSError: disk full",
            traceback="Traceback ...",
        )

        records = _read_lines(logger.log_path)
        r = records[0]
        assert r["level"] == "FATAL"
        assert r["error"] == "OSError: disk full"
        assert r["traceback"] == "Traceback ..."

    def test_error_traceback_omitted_when_not_provided(
        self, tmp_path: Path
    ) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.error("process", "Soft error", pipeline="p")

        records = _read_lines(logger.log_path)
        r = records[0]
        assert r["level"] == "ERROR"
        assert "traceback" not in r

    def test_error_preserves_source_and_node(self, tmp_path: Path) -> None:
        logger = FactoryJsonLogger(tmp_path)
        logger.error("process", "Handler failed", pipeline="etl", node="extract")

        records = _read_lines(logger.log_path)
        r = records[0]
        assert r["source"] == "process"
        assert r["node"] == "extract"
        assert r["pipeline"] == "etl"
