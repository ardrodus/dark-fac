"""Tests for the Obelisk log watcher — alert detection from structured logs.

Verifies that:
- Watcher detects ERROR log lines (source=runner) and creates Alert
- Watcher detects FATAL log lines and creates Alert
- Watcher ignores INFO/WARN lines (unless repeated WARN pattern)
- Signature is consistent for the same error
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dark_factory.obelisk.models import Alert
from dark_factory.obelisk.watcher import (
    WarnTracker,
    _compute_signature,
    _parse_line,
    _record_to_alert,
    _should_alert,
    tail_log,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _write_log_line(path: Path, record: dict) -> None:
    """Append a single JSON log line to *path*."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _make_record(
    level: str = "INFO",
    source: str = "runner",
    pipeline: str = "test-pipe",
    node: str = "node-1",
    msg: str = "something happened",
    **extra: object,
) -> dict:
    return {"level": level, "source": source, "pipeline": pipeline, "node": node, "msg": msg, **extra}


# ── Unit tests: _should_alert ────────────────────────────────────────


class TestShouldAlert:
    def test_error_from_runner_triggers(self) -> None:
        record = _make_record(level="ERROR", source="runner")
        assert _should_alert(record) is True

    def test_error_from_non_runner_does_not_trigger(self) -> None:
        record = _make_record(level="ERROR", source="llm")
        assert _should_alert(record) is False

    def test_fatal_always_triggers(self) -> None:
        record = _make_record(level="FATAL", source="runner")
        assert _should_alert(record) is True

    def test_fatal_non_runner_also_triggers(self) -> None:
        record = _make_record(level="FATAL", source="llm")
        assert _should_alert(record) is True

    def test_info_does_not_trigger(self) -> None:
        record = _make_record(level="INFO")
        assert _should_alert(record) is False

    def test_warn_does_not_trigger(self) -> None:
        record = _make_record(level="WARN")
        assert _should_alert(record) is False


# ── Unit tests: _compute_signature ───────────────────────────────────


class TestComputeSignature:
    def test_same_record_same_signature(self) -> None:
        r = _make_record(level="ERROR", source="runner", msg="OOM")
        assert _compute_signature(r) == _compute_signature(r)

    def test_identical_fields_produce_same_signature(self) -> None:
        r1 = _make_record(level="ERROR", source="runner", pipeline="p", node="n", msg="fail")
        r2 = _make_record(level="ERROR", source="runner", pipeline="p", node="n", msg="fail")
        assert _compute_signature(r1) == _compute_signature(r2)

    def test_different_message_different_signature(self) -> None:
        r1 = _make_record(level="ERROR", msg="OOM")
        r2 = _make_record(level="ERROR", msg="timeout")
        assert _compute_signature(r1) != _compute_signature(r2)

    def test_different_level_different_signature(self) -> None:
        r1 = _make_record(level="ERROR", msg="fail")
        r2 = _make_record(level="FATAL", msg="fail")
        assert _compute_signature(r1) != _compute_signature(r2)

    def test_signature_is_16_hex_chars(self) -> None:
        sig = _compute_signature(_make_record())
        assert len(sig) == 16
        assert all(c in "0123456789abcdef" for c in sig)

    def test_error_field_preferred_over_msg(self) -> None:
        r1 = _make_record(level="ERROR", msg="generic", error="specific crash")
        r2 = _make_record(level="ERROR", msg="generic")
        assert _compute_signature(r1) != _compute_signature(r2)


# ── Unit tests: _record_to_alert ─────────────────────────────────────


class TestRecordToAlert:
    def test_builds_alert_from_record(self) -> None:
        r = _make_record(level="ERROR", source="runner", pipeline="p", node="n", msg="boom")
        sig = _compute_signature(r)
        alert = _record_to_alert(r, sig)
        assert isinstance(alert, Alert)
        assert alert.error_type == "ERROR"
        assert alert.source == "runner"
        assert alert.pipeline == "p"
        assert alert.node == "n"
        assert alert.message == "boom"
        assert alert.signature == sig


# ── Unit tests: _parse_line ──────────────────────────────────────────


class TestParseLine:
    def test_valid_json(self) -> None:
        assert _parse_line('{"level": "INFO"}') == {"level": "INFO"}

    def test_invalid_json_returns_none(self) -> None:
        assert _parse_line("not json") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_line("") is None


# ── Unit tests: WarnTracker ──────────────────────────────────────────


class TestWarnTracker:
    def test_below_threshold_no_alert(self) -> None:
        tracker = WarnTracker(threshold=3)
        assert tracker.push("sig-a") is False
        assert tracker.push("sig-a") is False

    def test_meets_threshold_alerts(self) -> None:
        tracker = WarnTracker(threshold=3)
        tracker.push("sig-a")
        tracker.push("sig-a")
        assert tracker.push("sig-a") is True

    def test_different_signatures_counted_independently(self) -> None:
        tracker = WarnTracker(threshold=3)
        tracker.push("sig-a")
        tracker.push("sig-b")
        tracker.push("sig-a")
        assert tracker.push("sig-b") is False  # only 2 of sig-b

    def test_window_eviction(self) -> None:
        tracker = WarnTracker(threshold=3, window_size=4)
        tracker.push("sig-a")
        tracker.push("sig-a")
        # Fill window with other sigs to push out sig-a
        tracker.push("sig-x")
        tracker.push("sig-y")
        tracker.push("sig-z")
        # sig-a now only appears 0 times in window
        assert tracker.push("sig-a") is False


# ── Integration tests: tail_log ──────────────────────────────────────


class TestTailLogDetectsError:
    """Test: watcher detects ERROR log lines and creates Alert."""

    @pytest.mark.asyncio
    async def test_error_runner_creates_alert(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            _write_log_line(log_path, _make_record(level="ERROR", source="runner", msg="OOM killed"))
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 1
        assert alerts[0].error_type == "ERROR"
        assert alerts[0].message == "OOM killed"

    @pytest.mark.asyncio
    async def test_error_non_runner_ignored(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            _write_log_line(log_path, _make_record(level="ERROR", source="llm", msg="rate limit"))
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 0


class TestTailLogDetectsFatal:
    """Test: watcher detects FATAL log lines and creates Alert."""

    @pytest.mark.asyncio
    async def test_fatal_creates_alert(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            _write_log_line(log_path, _make_record(level="FATAL", source="runner", msg="unrecoverable"))
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 1
        assert alerts[0].error_type == "FATAL"
        assert alerts[0].message == "unrecoverable"

    @pytest.mark.asyncio
    async def test_fatal_any_source_creates_alert(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            _write_log_line(log_path, _make_record(level="FATAL", source="llm", msg="crash"))
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 1
        assert alerts[0].error_type == "FATAL"


class TestTailLogIgnoresInfoWarn:
    """Test: watcher ignores INFO/WARN lines (unless repeated WARN pattern)."""

    @pytest.mark.asyncio
    async def test_info_ignored(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            _write_log_line(log_path, _make_record(level="INFO", msg="all good"))
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_single_warn_ignored(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            _write_log_line(log_path, _make_record(level="WARN", msg="retrying"))
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 0

    @pytest.mark.asyncio
    async def test_repeated_warn_triggers_alert(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            warn = _make_record(level="WARN", msg="retrying")
            # Write 3 identical WARNs (meets default threshold)
            _write_log_line(log_path, warn)
            _write_log_line(log_path, warn)
            _write_log_line(log_path, warn)
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 1
        assert alerts[0].error_type == "WARN"


class TestSignatureConsistency:
    """Test: signature is consistent for the same error across tail_log runs."""

    @pytest.mark.asyncio
    async def test_same_error_same_signature(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            record = _make_record(level="ERROR", source="runner", msg="OOM killed")
            _write_log_line(log_path, record)
            _write_log_line(log_path, record)
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 2
        assert alerts[0].signature == alerts[1].signature

    @pytest.mark.asyncio
    async def test_different_errors_different_signatures(self, tmp_path: Path) -> None:
        log_path = tmp_path / "factory.jsonl"
        log_path.touch()

        alerts: list[Alert] = []

        async def on_alert(alert: Alert) -> None:
            alerts.append(alert)

        stop = asyncio.Event()

        async def write_and_stop() -> None:
            await asyncio.sleep(0.05)
            _write_log_line(log_path, _make_record(level="ERROR", source="runner", msg="OOM"))
            _write_log_line(log_path, _make_record(level="ERROR", source="runner", msg="timeout"))
            await asyncio.sleep(0.1)
            stop.set()

        await asyncio.gather(
            tail_log(log_path, on_alert, poll_interval=0.02, stop_event=stop),
            write_and_stop(),
        )

        assert len(alerts) == 2
        assert alerts[0].signature != alerts[1].signature
