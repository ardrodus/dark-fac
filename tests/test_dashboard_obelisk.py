"""Tests for dashboard Obelisk status display (US-017).

Verifies that:
- Dashboard reads and displays obelisk-status.json via load_obelisk_status
- Dashboard handles missing/corrupt status file gracefully
- Investigation list renders with verdict and URL
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import DataTable

from dark_factory.ui.dashboard import (
    DashboardApp,
    DashboardState,
    ObeliskInvestigation,
    ObeliskStatus,
)
from dark_factory.ui.status_reporter import load_obelisk_status

# ── load_obelisk_status: reads obelisk-status.json ──────────────────


class TestLoadObeliskStatus:
    """Test: dashboard reads and displays obelisk-status.json."""

    def test_reads_full_status(self, tmp_path: Path) -> None:
        """Parses all fields from a well-formed obelisk-status.json."""
        state_dir = tmp_path / ".dark-factory"
        state_dir.mkdir()
        payload = {
            "status": "watching",
            "dark_factory_pid": 12345,
            "uptime_s": 600.0,
            "crash_count": 2,
            "investigations": [
                {"id": "inv-abc", "verdict": "FIXED", "timestamp": 1000.0, "url": "https://github.com/org/repo/pull/42"},
                {"id": "inv-def", "verdict": "ESCALATED", "timestamp": 1001.0, "url": "https://github.com/org/repo/issues/99"},
            ],
        }
        (state_dir / "obelisk-status.json").write_text(json.dumps(payload), encoding="utf-8")

        result = load_obelisk_status(cwd=tmp_path)

        assert result.status == "watching"
        assert result.dark_factory_pid == 12345
        assert result.uptime_s == 600.0
        assert result.crash_count == 2
        assert len(result.investigations) == 2
        assert result.investigations[0].id == "inv-abc"
        assert result.investigations[0].verdict == "FIXED"
        assert result.investigations[0].url == "https://github.com/org/repo/pull/42"
        assert result.investigations[1].id == "inv-def"
        assert result.investigations[1].verdict == "ESCALATED"
        assert result.investigations[1].url == "https://github.com/org/repo/issues/99"

    def test_reads_status_without_url(self, tmp_path: Path) -> None:
        """Investigations without a url field default to empty string."""
        state_dir = tmp_path / ".dark-factory"
        state_dir.mkdir()
        payload = {
            "status": "watching",
            "dark_factory_pid": 100,
            "uptime_s": 10.0,
            "crash_count": 0,
            "investigations": [
                {"id": "inv-001", "verdict": "FIXED", "timestamp": 500.0},
            ],
        }
        (state_dir / "obelisk-status.json").write_text(json.dumps(payload), encoding="utf-8")

        result = load_obelisk_status(cwd=tmp_path)

        assert len(result.investigations) == 1
        assert result.investigations[0].url == ""

    def test_pid_none_when_absent(self, tmp_path: Path) -> None:
        """PID is None when dark_factory_pid is null in JSON."""
        state_dir = tmp_path / ".dark-factory"
        state_dir.mkdir()
        payload = {"status": "stopped", "dark_factory_pid": None, "uptime_s": 0, "crash_count": 0}
        (state_dir / "obelisk-status.json").write_text(json.dumps(payload), encoding="utf-8")

        result = load_obelisk_status(cwd=tmp_path)

        assert result.dark_factory_pid is None
        assert result.status == "stopped"

    def test_skips_malformed_investigations(self, tmp_path: Path) -> None:
        """Investigations missing required fields are silently skipped."""
        state_dir = tmp_path / ".dark-factory"
        state_dir.mkdir()
        payload = {
            "status": "watching",
            "investigations": [
                {"id": "inv-good", "verdict": "FIXED", "timestamp": 1.0},
                {"id": 123, "verdict": "BAD", "timestamp": 2.0},  # id not str
                {"verdict": "NOID", "timestamp": 3.0},  # missing id
                "not-a-dict",
            ],
        }
        (state_dir / "obelisk-status.json").write_text(json.dumps(payload), encoding="utf-8")

        result = load_obelisk_status(cwd=tmp_path)

        assert len(result.investigations) == 1
        assert result.investigations[0].id == "inv-good"


# ── load_obelisk_status: missing/corrupt file ───────────────────────


class TestLoadObeliskStatusMissing:
    """Test: dashboard handles missing status file gracefully."""

    def test_missing_file_returns_default(self, tmp_path: Path) -> None:
        """Returns default ObeliskStatus when file does not exist."""
        result = load_obelisk_status(cwd=tmp_path)

        assert result.status == "unknown"
        assert result.dark_factory_pid is None
        assert result.uptime_s == 0.0
        assert result.crash_count == 0
        assert result.investigations == ()

    def test_missing_directory_returns_default(self, tmp_path: Path) -> None:
        """Returns default ObeliskStatus when .dark-factory dir doesn't exist."""
        result = load_obelisk_status(cwd=tmp_path / "nonexistent")

        assert result.status == "unknown"
        assert result.investigations == ()

    def test_corrupt_json_returns_default(self, tmp_path: Path) -> None:
        """Returns default ObeliskStatus when file contains invalid JSON."""
        state_dir = tmp_path / ".dark-factory"
        state_dir.mkdir()
        (state_dir / "obelisk-status.json").write_text("not valid json{{{", encoding="utf-8")

        result = load_obelisk_status(cwd=tmp_path)

        assert result.status == "unknown"
        assert result.investigations == ()

    def test_empty_file_returns_default(self, tmp_path: Path) -> None:
        """Returns default ObeliskStatus when file is empty."""
        state_dir = tmp_path / ".dark-factory"
        state_dir.mkdir()
        (state_dir / "obelisk-status.json").write_text("", encoding="utf-8")

        result = load_obelisk_status(cwd=tmp_path)

        assert result.status == "unknown"


# ── Dashboard TUI: investigation list renders with verdict and URL ───


class TestInvestigationListRendering:
    """Test: investigation list renders with verdict and URL."""

    @pytest.mark.asyncio
    async def test_investigation_table_shows_verdict_and_url(self) -> None:
        """HealthPanel investigation table includes id, verdict, and URL columns."""
        obelisk = ObeliskStatus(
            status="watching",
            dark_factory_pid=42,
            uptime_s=300.0,
            crash_count=0,
            investigations=(
                ObeliskInvestigation(id="inv-001", verdict="FIXED", timestamp=1000.0, url="https://github.com/org/repo/pull/1"),
                ObeliskInvestigation(id="inv-002", verdict="ESCALATED", timestamp=1001.0, url="https://github.com/org/repo/issues/5"),
            ),
        )
        state = DashboardState(obelisk=obelisk)
        app = DashboardApp(state=state)
        async with app.run_test():
            inv_table = app.query_one("#obelisk-inv-table", DataTable)
            assert inv_table.row_count == 2
            # Columns: Investigation, Verdict, URL
            assert len(inv_table.columns) == 3

    @pytest.mark.asyncio
    async def test_investigation_table_empty_when_no_investigations(self) -> None:
        """Investigation table has zero rows with default (empty) ObeliskStatus."""
        state = DashboardState()
        app = DashboardApp(state=state)
        async with app.run_test():
            inv_table = app.query_one("#obelisk-inv-table", DataTable)
            assert inv_table.row_count == 0

    @pytest.mark.asyncio
    async def test_obelisk_summary_shows_status(self) -> None:
        """Obelisk summary label includes the supervisor state."""
        obelisk = ObeliskStatus(
            status="crashed",
            dark_factory_pid=None,
            uptime_s=0.0,
            crash_count=3,
        )
        state = DashboardState(obelisk=obelisk)
        app = DashboardApp(state=state)
        async with app.run_test():
            from textual.widgets import Label

            summary = app.query_one("#obelisk-summary", Label)
            rendered = summary.content
            assert "crashed" in rendered
            assert "3" in rendered

    @pytest.mark.asyncio
    async def test_investigation_url_dash_when_empty(self) -> None:
        """URL column shows '-' when investigation has no URL."""
        obelisk = ObeliskStatus(
            status="watching",
            investigations=(
                ObeliskInvestigation(id="inv-no-url", verdict="FIXED", timestamp=1.0, url=""),
            ),
        )
        state = DashboardState(obelisk=obelisk)
        app = DashboardApp(state=state)
        async with app.run_test():
            inv_table = app.query_one("#obelisk-inv-table", DataTable)
            assert inv_table.row_count == 1

    @pytest.mark.asyncio
    async def test_investigation_table_limited_to_last_five(self) -> None:
        """Dashboard only shows the last 5 investigations."""
        invs = tuple(
            ObeliskInvestigation(id=f"inv-{i:03d}", verdict="FIXED", timestamp=float(i))
            for i in range(10)
        )
        obelisk = ObeliskStatus(status="watching", investigations=invs)
        state = DashboardState(obelisk=obelisk)
        app = DashboardApp(state=state)
        async with app.run_test():
            inv_table = app.query_one("#obelisk-inv-table", DataTable)
            # HealthPanel.refresh_health slices [-5:]
            assert inv_table.row_count == 5
