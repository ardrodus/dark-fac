"""Three-tier dedup cache for Obelisk investigations.

Prevents redundant investigations by checking three tiers:
  L1 — In-memory dict (cleared on restart)
  L2 — Disk JSONL file (2-week retention)
  L3 — GitHub Issues (``gh issue list --label obelisk --search``)

An investigation only runs if all three tiers miss.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from dark_factory.integrations.shell import gh

logger = logging.getLogger(__name__)

# L2 disk file lives under .dark-factory/obelisk/
_INVESTIGATIONS_FILENAME = "investigations.jsonl"

# L2 entries older than this are pruned on load.
_RETENTION_SECONDS = 14 * 24 * 60 * 60  # 2 weeks


class DedupCache:
    """Three-tier dedup cache for alert signatures.

    Parameters
    ----------
    workspace:
        Path to the factory workspace (parent of ``.dark-factory/``).
    repo:
        GitHub ``owner/repo`` for L3 lookups.  If ``None``, L3 is skipped.
    """

    def __init__(self, workspace: str, *, repo: str | None = None) -> None:
        self._l1: dict[str, float] = {}
        self._workspace = Path(workspace)
        self._repo = repo
        self._disk_path = self._workspace / ".dark-factory" / "obelisk" / _INVESTIGATIONS_FILENAME

    # ── public API ──────────────────────────────────────────────────

    def check(self, signature: str) -> str | None:
        """Check all tiers for *signature*.

        Returns the tier name (``"L1"``, ``"L2"``, ``"L3"``) if the
        signature is known, or ``None`` if all tiers miss.
        """
        if self._check_l1(signature):
            logger.debug("Dedup SKIP (L1 in-memory) for %s", signature)
            return "L1"

        if self._check_l2(signature):
            logger.debug("Dedup SKIP (L2 disk) for %s", signature)
            return "L2"

        if self._check_l3(signature):
            logger.debug("Dedup SKIP (L3 GitHub) for %s", signature)
            return "L3"

        return None

    def record(self, signature: str) -> None:
        """Record a signature after a successful investigation."""
        now = time.time()
        self._l1[signature] = now
        self._append_l2(signature, now)

    # ── L1: in-memory ───────────────────────────────────────────────

    def _check_l1(self, signature: str) -> bool:
        return signature in self._l1

    # ── L2: disk JSONL ──────────────────────────────────────────────

    def _check_l2(self, signature: str) -> bool:
        cutoff = time.time() - _RETENTION_SECONDS
        try:
            lines = self._disk_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False

        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("signature") == signature and entry.get("ts", 0) >= cutoff:
                # Promote to L1
                self._l1[signature] = entry["ts"]
                return True
        return False

    def _append_l2(self, signature: str, ts: float) -> None:
        self._disk_path.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({"signature": signature, "ts": ts})
        with self._disk_path.open("a", encoding="utf-8") as f:
            f.write(entry + "\n")

    # ── L3: GitHub Issues ───────────────────────────────────────────

    def _check_l3(self, signature: str) -> bool:
        if not self._repo:
            return False
        try:
            result = gh(
                [
                    "issue",
                    "list",
                    "--label",
                    "obelisk",
                    "--search",
                    signature,
                    "--json",
                    "number",
                    "--limit",
                    "1",
                    "--repo",
                    self._repo,
                ],
                timeout=15,
            )
            if result.returncode != 0:
                logger.debug("L3 gh issue list failed: %s", result.stderr.strip())
                return False
            issues = json.loads(result.stdout)
            if issues:
                # Promote to L1
                self._l1[signature] = time.time()
                return True
        except Exception:
            logger.debug("L3 GitHub check failed for %s", signature, exc_info=True)
        return False
