"""Tests for the three-tier DedupCache.

Verifies that:
- L1 in-memory cache hits skip investigation
- L2 disk cache hits skip investigation
- L3 GitHub cache hits skip investigation
- All-miss triggers full investigation (check returns None)
- L2 retention purges entries older than 2 weeks
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from dark_factory.integrations.shell import CommandResult
from dark_factory.obelisk.cache import _RETENTION_SECONDS, DedupCache

# ── L1: in-memory cache ────────────────────────────────────────────


class TestL1InMemoryCache:
    """L1 in-memory hits return "L1" and skip further tiers."""

    def test_l1_hit_after_record(self, tmp_path: Path) -> None:
        cache = DedupCache(str(tmp_path))
        cache.record("sig::A")

        assert cache.check("sig::A") == "L1"

    def test_l1_miss_for_unknown_signature(self, tmp_path: Path) -> None:
        cache = DedupCache(str(tmp_path))

        assert cache.check("sig::unknown") is None

    def test_l1_different_signatures_independent(self, tmp_path: Path) -> None:
        cache = DedupCache(str(tmp_path))
        cache.record("sig::A")

        assert cache.check("sig::A") == "L1"
        assert cache.check("sig::B") is None


# ── L2: disk JSONL cache ──────────────────────────────────────────


class TestL2DiskCache:
    """L2 disk hits return "L2" and promote to L1."""

    def test_l2_hit_from_disk_file(self, tmp_path: Path) -> None:
        """A signature recorded by one cache instance is found via disk
        by a fresh instance (simulating restart)."""
        cache1 = DedupCache(str(tmp_path))
        cache1.record("sig::disk")

        # Fresh instance — L1 is empty, but L2 file exists
        cache2 = DedupCache(str(tmp_path))
        assert cache2.check("sig::disk") == "L2"

    def test_l2_hit_promotes_to_l1(self, tmp_path: Path) -> None:
        """After an L2 hit, subsequent checks should return L1."""
        cache1 = DedupCache(str(tmp_path))
        cache1.record("sig::promote")

        cache2 = DedupCache(str(tmp_path))
        assert cache2.check("sig::promote") == "L2"
        # Now promoted to L1
        assert cache2.check("sig::promote") == "L1"

    def test_l2_miss_when_no_disk_file(self, tmp_path: Path) -> None:
        """No disk file → L2 misses gracefully."""
        cache = DedupCache(str(tmp_path))
        # No record, no file — must fall through L2 without error
        assert cache.check("sig::nope") is None

    def test_l2_retention_purges_old_entries(self, tmp_path: Path) -> None:
        """Entries older than 2 weeks are ignored by L2 check."""
        # Write a disk entry with a timestamp > 2 weeks ago
        disk_path = (
            tmp_path / ".dark-factory" / "obelisk" / "investigations.jsonl"
        )
        disk_path.parent.mkdir(parents=True, exist_ok=True)

        old_ts = time.time() - _RETENTION_SECONDS - 3600  # 1 hour past cutoff
        entry = json.dumps({"signature": "sig::old", "ts": old_ts})
        disk_path.write_text(entry + "\n", encoding="utf-8")

        cache = DedupCache(str(tmp_path))
        assert cache.check("sig::old") is None  # too old, should miss

    def test_l2_retention_keeps_recent_entries(self, tmp_path: Path) -> None:
        """Entries within the 2-week window are found."""
        disk_path = (
            tmp_path / ".dark-factory" / "obelisk" / "investigations.jsonl"
        )
        disk_path.parent.mkdir(parents=True, exist_ok=True)

        recent_ts = time.time() - 3600  # 1 hour ago
        entry = json.dumps({"signature": "sig::recent", "ts": recent_ts})
        disk_path.write_text(entry + "\n", encoding="utf-8")

        cache = DedupCache(str(tmp_path))
        assert cache.check("sig::recent") == "L2"


# ── L3: GitHub Issues cache ──────────────────────────────────────


class TestL3GitHubCache:
    """L3 GitHub hits return "L3" and promote to L1."""

    def test_l3_hit_when_issue_exists(self, tmp_path: Path) -> None:
        """gh issue list returns a matching issue → L3 hit."""
        cache = DedupCache(str(tmp_path), repo="org/repo")

        gh_result = CommandResult(
            stdout=json.dumps([{"number": 42}]),
            stderr="",
            returncode=0,
            duration_ms=500,
        )
        with patch("dark_factory.obelisk.cache.gh", return_value=gh_result):
            assert cache.check("sig::github") == "L3"

    def test_l3_hit_promotes_to_l1(self, tmp_path: Path) -> None:
        """After an L3 hit, subsequent checks should return L1."""
        cache = DedupCache(str(tmp_path), repo="org/repo")

        gh_result = CommandResult(
            stdout=json.dumps([{"number": 42}]),
            stderr="",
            returncode=0,
            duration_ms=500,
        )
        with patch("dark_factory.obelisk.cache.gh", return_value=gh_result):
            assert cache.check("sig::gh_promote") == "L3"

        # Now promoted to L1 — no need to mock gh again
        assert cache.check("sig::gh_promote") == "L1"

    def test_l3_miss_when_no_issues(self, tmp_path: Path) -> None:
        """gh issue list returns empty list → L3 miss."""
        cache = DedupCache(str(tmp_path), repo="org/repo")

        gh_result = CommandResult(
            stdout=json.dumps([]),
            stderr="",
            returncode=0,
            duration_ms=300,
        )
        with patch("dark_factory.obelisk.cache.gh", return_value=gh_result):
            assert cache.check("sig::nobody") is None

    def test_l3_skipped_when_no_repo(self, tmp_path: Path) -> None:
        """Without a repo, L3 is skipped entirely."""
        cache = DedupCache(str(tmp_path))  # no repo

        # Should not call gh at all — check returns None
        assert cache.check("sig::norepo") is None

    def test_l3_miss_on_gh_failure(self, tmp_path: Path) -> None:
        """Non-zero exit from gh → L3 miss (no crash)."""
        cache = DedupCache(str(tmp_path), repo="org/repo")

        gh_result = CommandResult(
            stdout="",
            stderr="error: something went wrong",
            returncode=1,
            duration_ms=100,
        )
        with patch("dark_factory.obelisk.cache.gh", return_value=gh_result):
            assert cache.check("sig::fail") is None


# ── All-miss scenario ────────────────────────────────────────────


class TestAllMiss:
    """When no tier has the signature, check() returns None."""

    def test_all_tiers_miss(self, tmp_path: Path) -> None:
        """Fresh cache with repo configured — all three tiers miss."""
        cache = DedupCache(str(tmp_path), repo="org/repo")

        gh_result = CommandResult(
            stdout=json.dumps([]),
            stderr="",
            returncode=0,
            duration_ms=200,
        )
        with patch("dark_factory.obelisk.cache.gh", return_value=gh_result):
            assert cache.check("sig::brand_new") is None

    def test_all_tiers_miss_no_repo(self, tmp_path: Path) -> None:
        """Fresh cache without repo — L1 miss, L2 miss, L3 skipped."""
        cache = DedupCache(str(tmp_path))
        assert cache.check("sig::brand_new") is None
