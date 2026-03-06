"""Tests for the Obelisk supervisor spawn/restart logic.

Verifies that:
- Supervisor spawns a subprocess via _spawn_factory
- Supervisor restarts on crash (non-zero exit)
- Supervisor handles missing active repo gracefully (dispatch level)
- Supervisor works without factory_repo (degraded mode, uses cwd)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from dark_factory.obelisk.supervisor import (
    CRASH_LOOP_THRESHOLD,
    CRASH_LOOP_WINDOW,
    InvestigationSummary,
    SupervisorState,
    _build_command,
    _find_checkpoint,
    _is_alive,
    _record_crash,
    _write_status,
    run_supervisor,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _mock_process(
    *,
    pid: int = 12345,
    poll_sequence: list[int | None] | None = None,
    returncode: int = 0,
) -> MagicMock:
    """Create a mock subprocess.Popen with configurable poll() behaviour.

    *poll_sequence* controls what poll() returns on successive calls.
    The default is ``[None, 0]`` — alive once, then exits cleanly.
    """
    proc = MagicMock()
    proc.pid = pid
    seq = poll_sequence if poll_sequence is not None else [None, returncode]
    proc.poll = MagicMock(side_effect=seq)
    proc.returncode = returncode
    proc.stderr = MagicMock()
    proc.stderr.read = MagicMock(return_value=b"")
    proc.terminate = MagicMock()
    proc.wait = MagicMock()
    proc.kill = MagicMock()
    return proc


# ── Unit tests: helpers ──────────────────────────────────────────────


class TestBuildCommand:
    def test_basic_command(self) -> None:
        cmd = _build_command("owner/repo")
        assert "--repo" in cmd
        assert "owner/repo" in cmd
        assert "--auto" in cmd
        assert "--resume" not in cmd

    def test_with_checkpoint(self, tmp_path: Path) -> None:
        ckpt = tmp_path / "checkpoint.json"
        cmd = _build_command("owner/repo", checkpoint=ckpt)
        assert "--resume" in cmd
        assert str(ckpt) in cmd


class TestFindCheckpoint:
    def test_no_checkpoint_file(self, tmp_path: Path) -> None:
        assert _find_checkpoint(tmp_path) is None

    def test_checkpoint_resumable(self, tmp_path: Path) -> None:
        ckpt_dir = tmp_path / ".dark-factory"
        ckpt_dir.mkdir()
        ckpt = ckpt_dir / ".checkpoint.json"
        ckpt.write_text(json.dumps({"status": "running"}), encoding="utf-8")
        assert _find_checkpoint(tmp_path) == ckpt

    def test_checkpoint_cancelled_is_resumable(self, tmp_path: Path) -> None:
        ckpt_dir = tmp_path / ".dark-factory"
        ckpt_dir.mkdir()
        ckpt = ckpt_dir / ".checkpoint.json"
        ckpt.write_text(json.dumps({"status": "cancelled"}), encoding="utf-8")
        assert _find_checkpoint(tmp_path) == ckpt

    def test_checkpoint_completed_not_resumable(self, tmp_path: Path) -> None:
        ckpt_dir = tmp_path / ".dark-factory"
        ckpt_dir.mkdir()
        ckpt = ckpt_dir / ".checkpoint.json"
        ckpt.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        assert _find_checkpoint(tmp_path) is None

    def test_checkpoint_invalid_json(self, tmp_path: Path) -> None:
        ckpt_dir = tmp_path / ".dark-factory"
        ckpt_dir.mkdir()
        ckpt = ckpt_dir / ".checkpoint.json"
        ckpt.write_text("not json", encoding="utf-8")
        assert _find_checkpoint(tmp_path) is None


class TestIsAlive:
    def test_alive(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = None
        assert _is_alive(proc) is True

    def test_dead(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = 0
        assert _is_alive(proc) is False


class TestWriteStatus:
    def test_writes_json(self, tmp_path: Path) -> None:
        state = SupervisorState(pid=42, status="watching", restarts=1, start_time=1000.0)
        _write_status(state, "owner/repo", tmp_path)
        path = tmp_path / "obelisk-status.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["dark_factory_pid"] == 42
        assert data["status"] == "watching"
        assert data["crash_count"] == 1
        assert data["uptime_s"] > 0
        assert data["investigations"] == []
        assert data["repo"] == "owner/repo"
        assert "timestamp" in data

    def test_creates_directory(self, tmp_path: Path) -> None:
        status_dir = tmp_path / "subdir"
        state = SupervisorState()
        _write_status(state, "owner/repo", status_dir)
        assert (status_dir / "obelisk-status.json").exists()

    def test_writes_investigations(self, tmp_path: Path) -> None:
        state = SupervisorState(status="watching", start_time=1000.0)
        state.investigations.append(
            InvestigationSummary(id="inv-abc", verdict="FIXED", timestamp=1001.0),
        )
        _write_status(state, "owner/repo", tmp_path)
        data = json.loads((tmp_path / "obelisk-status.json").read_text(encoding="utf-8"))
        assert len(data["investigations"]) == 1
        assert data["investigations"][0]["id"] == "inv-abc"
        assert data["investigations"][0]["verdict"] == "FIXED"


# ── Integration tests: run_supervisor ────────────────────────────────


_UI_PATCHES = {
    "dark_factory.obelisk.supervisor.cprint": MagicMock(),
    "dark_factory.obelisk.supervisor.print_error": MagicMock(),
    "dark_factory.obelisk.supervisor.print_stage_result": MagicMock(),
    "dark_factory.obelisk.supervisor.spinner": MagicMock(),
}


def _patch_ui():
    """Context manager that silences all UI output from the supervisor."""
    import contextlib

    # spinner is used as a context manager — mock must support `with`
    spinner_cm = MagicMock()
    spinner_cm.__enter__ = MagicMock(return_value=None)
    spinner_cm.__exit__ = MagicMock(return_value=False)

    patches = {
        "dark_factory.obelisk.supervisor.cprint": MagicMock(),
        "dark_factory.obelisk.supervisor.print_error": MagicMock(),
        "dark_factory.obelisk.supervisor.print_stage_result": MagicMock(),
        "dark_factory.obelisk.supervisor.spinner": MagicMock(return_value=spinner_cm),
    }
    return contextlib.ExitStack(), patches


class TestRunSupervisorSpawn:
    """Test: supervisor spawns a mock subprocess."""

    @patch("dark_factory.obelisk.supervisor.time.sleep", side_effect=lambda _: None)
    def test_spawns_subprocess_and_exits_cleanly(
        self, _mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        # Process that exits immediately with code 0
        proc = _mock_process(pid=9999, poll_sequence=[0], returncode=0)

        spinner_cm = MagicMock()
        spinner_cm.__enter__ = MagicMock(return_value=None)
        spinner_cm.__exit__ = MagicMock(return_value=False)

        with (
            patch("dark_factory.obelisk.supervisor.cprint"),
            patch("dark_factory.obelisk.supervisor.print_stage_result") as mock_result,
            patch("dark_factory.obelisk.supervisor.spinner", return_value=spinner_cm),
            patch("dark_factory.obelisk.supervisor._spawn_factory", return_value=proc) as mock_spawn,
        ):
            run_supervisor("owner/repo", factory_repo=tmp_path)

        mock_spawn.assert_called_once()
        # Verify it reported clean exit
        mock_result.assert_any_call("Dark Factory", "PASS", "exited cleanly")

        # Status file written
        status_path = tmp_path / ".dark-factory" / "obelisk-status.json"
        assert status_path.exists()
        data = json.loads(status_path.read_text(encoding="utf-8"))
        assert data["status"] == "stopped"


class TestRunSupervisorRestart:
    """Test: supervisor restarts on crash."""

    @patch("dark_factory.obelisk.supervisor.time.sleep", side_effect=lambda _: None)
    def test_restarts_after_crash_then_clean_exit(
        self, _mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        # First spawn: crash with exit code 1; second spawn: clean exit 0
        crash_proc = _mock_process(pid=111, poll_sequence=[1], returncode=1)
        ok_proc = _mock_process(pid=222, poll_sequence=[0], returncode=0)

        spinner_cm = MagicMock()
        spinner_cm.__enter__ = MagicMock(return_value=None)
        spinner_cm.__exit__ = MagicMock(return_value=False)

        with (
            patch("dark_factory.obelisk.supervisor.cprint") as mock_cprint,
            patch("dark_factory.obelisk.supervisor.print_stage_result"),
            patch("dark_factory.obelisk.supervisor.spinner", return_value=spinner_cm),
            patch(
                "dark_factory.obelisk.supervisor._spawn_factory",
                side_effect=[crash_proc, ok_proc],
            ) as mock_spawn,
        ):
            run_supervisor("owner/repo", factory_repo=tmp_path)

        # Spawned twice: once crashed, once clean
        assert mock_spawn.call_count == 2

        # Verify restart was announced
        restart_calls = [
            c for c in mock_cprint.call_args_list
            if "Restarting" in str(c)
        ]
        assert len(restart_calls) >= 1

        # Status file should show stopped (clean exit after restart)
        data = json.loads(
            (tmp_path / ".dark-factory" / "obelisk-status.json").read_text(encoding="utf-8")
        )
        assert data["status"] == "stopped"


class TestRunSupervisorMissingRepo:
    """Test: supervisor handles missing active repo gracefully (dispatch level)."""

    def test_dispatch_returns_on_missing_repo(self) -> None:
        """_run_obelisk_interactive returns early when no active repo."""
        with (
            patch(
                "dark_factory.cli.dispatch._resolve_active_repo",
                return_value="",
            ),
            patch("dark_factory.cli.dispatch.input", return_value=""),
            patch("dark_factory.ui.cli_colors.print_error") as mock_err,
        ):
            from dark_factory.cli.dispatch import _run_obelisk_interactive

            _run_obelisk_interactive()

        mock_err.assert_called_once()
        assert "No active repo" in mock_err.call_args[0][0]


class TestRunSupervisorDegradedMode:
    """Test: supervisor works without factory_repo (degraded mode, uses cwd)."""

    @patch("dark_factory.obelisk.supervisor.time.sleep", side_effect=lambda _: None)
    def test_runs_with_factory_repo_none(
        self, _mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        """When factory_repo is None, supervisor defaults to cwd."""
        proc = _mock_process(pid=555, poll_sequence=[0], returncode=0)

        spinner_cm = MagicMock()
        spinner_cm.__enter__ = MagicMock(return_value=None)
        spinner_cm.__exit__ = MagicMock(return_value=False)

        with (
            patch("dark_factory.obelisk.supervisor.cprint"),
            patch("dark_factory.obelisk.supervisor.print_stage_result"),
            patch("dark_factory.obelisk.supervisor.spinner", return_value=spinner_cm),
            patch("dark_factory.obelisk.supervisor._spawn_factory", return_value=proc) as mock_spawn,
            patch("dark_factory.obelisk.supervisor.Path") as mock_path_cls,
        ):
            # Make Path.cwd() return our tmp_path
            mock_path_cls.cwd.return_value = tmp_path
            mock_path_cls.side_effect = Path  # Path(x) still works normally

            run_supervisor("owner/repo", factory_repo=None)

        mock_spawn.assert_called_once()
        # The command should have been built with "owner/repo"
        cmd_arg = mock_spawn.call_args[0][0]
        assert "owner/repo" in cmd_arg

    @patch("dark_factory.obelisk.supervisor.time.sleep", side_effect=lambda _: None)
    def test_dispatch_passes_none_when_ouroboros_unconfigured(
        self, _mock_sleep: MagicMock,
    ) -> None:
        """Dispatch passes factory_repo=None when Ouroboros is not configured."""
        with (
            patch("dark_factory.cli.dispatch._resolve_active_repo", return_value="owner/repo"),
            patch("dark_factory.cli.dispatch._resolve_ouroboros_repo", return_value=""),
            patch("dark_factory.cli.dispatch.input", return_value=""),
            patch("dark_factory.ui.cli_colors.cprint"),
            patch("dark_factory.obelisk.supervisor.run_supervisor") as mock_run,
        ):
            from dark_factory.cli.dispatch import _run_obelisk_interactive

            _run_obelisk_interactive()

        mock_run.assert_called_once_with("owner/repo", factory_repo=None)


# ── Unit tests: crash loop detection ─────────────────────────────────


class TestRecordCrash:
    """Test: _record_crash sliding window crash counter."""

    def test_single_crash_no_loop(self) -> None:
        state = SupervisorState()
        assert _record_crash(state, now=1000.0) is False
        assert len(state.crash_timestamps) == 1

    def test_two_crashes_no_loop(self) -> None:
        state = SupervisorState()
        _record_crash(state, now=1000.0)
        assert _record_crash(state, now=1001.0) is False

    def test_three_crashes_in_window_triggers_loop(self) -> None:
        state = SupervisorState()
        _record_crash(state, now=1000.0)
        _record_crash(state, now=1100.0)
        assert _record_crash(state, now=1200.0) is True
        assert len(state.crash_timestamps) == 3

    def test_old_crashes_outside_window_pruned(self) -> None:
        state = SupervisorState()
        # Two crashes long ago (outside 5-minute window)
        _record_crash(state, now=100.0)
        _record_crash(state, now=101.0)
        # One recent crash — should NOT be a loop (old ones pruned)
        assert _record_crash(state, now=100.0 + CRASH_LOOP_WINDOW + 1) is False
        # Only 1 timestamp should remain after pruning
        assert len(state.crash_timestamps) == 1

    def test_three_crashes_exactly_at_window_boundary(self) -> None:
        state = SupervisorState()
        # First crash at t=0, second at t=150, third at exactly window edge
        _record_crash(state, now=1000.0)
        _record_crash(state, now=1000.0 + CRASH_LOOP_WINDOW / 2)
        # Third crash exactly at the window boundary — first crash is still inside
        assert _record_crash(state, now=1000.0 + CRASH_LOOP_WINDOW - 1) is True

    def test_threshold_matches_constant(self) -> None:
        assert CRASH_LOOP_THRESHOLD == 3
        assert CRASH_LOOP_WINDOW == 300.0


# ── Integration tests: crash loop stops supervisor ───────────────────


class TestRunSupervisorCrashLoop:
    """Test: supervisor stops restarting after crash loop."""

    @patch("dark_factory.obelisk.supervisor.time.sleep", side_effect=lambda _: None)
    def test_crash_loop_stops_restart(
        self, _mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        """Three rapid crashes trigger crash loop — supervisor exits."""
        # Three processes that crash immediately
        procs = [_mock_process(pid=i, poll_sequence=[1], returncode=1) for i in range(10, 13)]

        spinner_cm = MagicMock()
        spinner_cm.__enter__ = MagicMock(return_value=None)
        spinner_cm.__exit__ = MagicMock(return_value=False)

        with (
            patch("dark_factory.obelisk.supervisor.cprint") as mock_cprint,
            patch("dark_factory.obelisk.supervisor.print_stage_result"),
            patch("dark_factory.obelisk.supervisor.spinner", return_value=spinner_cm),
            patch(
                "dark_factory.obelisk.supervisor._spawn_factory",
                side_effect=procs,
            ) as mock_spawn,
        ):
            run_supervisor("owner/repo", factory_repo=tmp_path)

        # Should have spawned exactly 3 times, then stopped
        assert mock_spawn.call_count == 3

        # Verify crash loop was announced
        loop_calls = [
            c for c in mock_cprint.call_args_list
            if "CRASH LOOP" in str(c)
        ]
        assert len(loop_calls) >= 1

        # Status file should show crash_loop
        data = json.loads(
            (tmp_path / ".dark-factory" / "obelisk-status.json").read_text(encoding="utf-8"),
        )
        assert data["status"] == "crash_loop"

    @patch("dark_factory.obelisk.supervisor.time.sleep", side_effect=lambda _: None)
    def test_crash_loop_status_includes_investigation(
        self, _mock_sleep: MagicMock, tmp_path: Path,
    ) -> None:
        """Crash loop writes an investigation summary to the status file."""
        procs = [_mock_process(pid=i, poll_sequence=[1], returncode=1) for i in range(20, 23)]

        spinner_cm = MagicMock()
        spinner_cm.__enter__ = MagicMock(return_value=None)
        spinner_cm.__exit__ = MagicMock(return_value=False)

        with (
            patch("dark_factory.obelisk.supervisor.cprint"),
            patch("dark_factory.obelisk.supervisor.print_stage_result"),
            patch("dark_factory.obelisk.supervisor.spinner", return_value=spinner_cm),
            patch(
                "dark_factory.obelisk.supervisor._spawn_factory",
                side_effect=procs,
            ),
        ):
            run_supervisor("owner/repo", factory_repo=tmp_path)

        data = json.loads(
            (tmp_path / ".dark-factory" / "obelisk-status.json").read_text(encoding="utf-8"),
        )
        assert data["status"] == "crash_loop"
        assert len(data["investigations"]) >= 1
        assert data["investigations"][-1]["verdict"] == "CRASH_LOOP"
