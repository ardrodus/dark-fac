"""Obelisk log watcher — async tail of structured logs with alert creation.

Tails ``factory.jsonl`` and fires alerts for failure patterns:
- ERROR lines where ``source == "runner"``
- Any FATAL line
- Repeated WARN patterns (same signature seen N+ times in a window)

Detection only — no analysis or investigation logic.  The
:func:`make_investigation_handler` factory wires cache checks and
the investigator into the alert callback used by :func:`tail_log`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dark_factory.obelisk.cache import DedupCache
from dark_factory.obelisk.models import Alert

logger = logging.getLogger(__name__)

# How often (seconds) to poll the log file for new lines.
_POLL_INTERVAL = 0.5

# Number of repeated WARN signatures within the window to trigger an alert.
_WARN_REPEAT_THRESHOLD = 3

# Rolling window size (number of recent WARN signatures to track).
_WARN_WINDOW_SIZE = 50

# Type alias for the investigator callback.
AlertCallback = Callable[[Alert], Awaitable[None]]


def _compute_signature(record: dict[str, Any]) -> str:
    """Compute a stable dedup hash from key log fields."""
    parts = "|".join(
        [
            record.get("level", ""),
            record.get("source", ""),
            record.get("pipeline", ""),
            record.get("node", ""),
            record.get("error", record.get("msg", "")),
        ]
    )
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


def _record_to_alert(record: dict[str, Any], signature: str) -> Alert:
    """Build an Alert from a parsed log record."""
    return Alert(
        error_type=record.get("level", "UNKNOWN"),
        source=record.get("source", ""),
        pipeline=record.get("pipeline", ""),
        node=record.get("node", ""),
        message=record.get("msg", ""),
        signature=signature,
    )


def _should_alert(record: dict[str, Any]) -> bool:
    """Return True if a single log record warrants an immediate alert."""
    level = record.get("level", "")
    source = record.get("source", "")
    if level == "FATAL":
        return True
    if level == "ERROR" and source == "runner":
        return True
    return False


@dataclass
class WarnTracker:
    """Sliding-window tracker for repeated WARN signatures."""

    threshold: int = _WARN_REPEAT_THRESHOLD
    window_size: int = _WARN_WINDOW_SIZE
    _recent: list[str] = field(default_factory=list)

    def push(self, signature: str) -> bool:
        """Record a WARN signature; return True if threshold is met."""
        self._recent.append(signature)
        if len(self._recent) > self.window_size:
            self._recent = self._recent[-self.window_size :]
        counts: Counter[str] = Counter(self._recent)
        return counts[signature] >= self.threshold


async def tail_log(
    log_path: Path,
    on_alert: AlertCallback,
    *,
    poll_interval: float = _POLL_INTERVAL,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Async-tail a JSONL log file, firing alerts for failure patterns.

    Parameters
    ----------
    log_path:
        Path to the ``factory.jsonl`` file.
    on_alert:
        Async callback invoked with each detected ``Alert``.
    poll_interval:
        Seconds between file polls.
    stop_event:
        If provided, the watcher exits when this event is set.
    """
    warn_tracker = WarnTracker()
    offset = 0

    # Start from end of existing file so we only process new lines.
    try:
        offset = log_path.stat().st_size
    except OSError:
        pass

    while True:
        if stop_event is not None and stop_event.is_set():
            break

        lines = _read_new_lines(log_path, offset)
        if lines:
            new_offset, raw_lines = lines
            offset = new_offset

            for raw in raw_lines:
                record = _parse_line(raw)
                if record is None:
                    continue

                signature = _compute_signature(record)

                if _should_alert(record):
                    await on_alert(_record_to_alert(record, signature))
                elif record.get("level") == "WARN":
                    if warn_tracker.push(signature):
                        await on_alert(_record_to_alert(record, signature))

        await asyncio.sleep(poll_interval)


def _read_new_lines(log_path: Path, offset: int) -> tuple[int, list[str]] | None:
    """Read new lines from *log_path* starting at *offset*.

    Returns ``(new_offset, lines)`` or ``None`` if nothing new.
    """
    try:
        size = log_path.stat().st_size
    except OSError:
        return None

    if size <= offset:
        return None

    try:
        with log_path.open("r", encoding="utf-8") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return None

    lines = [ln for ln in data.splitlines() if ln.strip()]
    if not lines:
        return None
    return size, lines


def _parse_line(raw: str) -> dict[str, Any] | None:
    """Parse a single JSON log line, returning None on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


# ── Watcher → Cache → Investigator wiring ────────────────────────────


def make_investigation_handler(
    factory_workspace: str,
    user_workspace: str,
    *,
    repo: str | None = None,
    dedup_cache: DedupCache | None = None,
) -> AlertCallback:
    """Create an :data:`AlertCallback` wired with cache-then-investigate logic.

    The returned callback is suitable for passing as *on_alert* to
    :func:`tail_log`.  For each alert it:

    1. Checks the dedup cache.
    2. On cache hit — logs the matching tier and returns (skip).
    3. On cache miss — delegates to :func:`~dark_factory.obelisk.investigator.investigate`.

    Parameters
    ----------
    factory_workspace:
        Path to the factory repo workspace (writable).
    user_workspace:
        Path to the user repo workspace (read-only context).
    repo:
        GitHub ``owner/repo`` for L3 cache lookups and investigation.
    dedup_cache:
        Pre-built cache instance.  If ``None`` one is created from
        *factory_workspace* and *repo*.
    """
    cache = dedup_cache or DedupCache(factory_workspace, repo=repo)

    async def _on_alert(alert: Alert) -> None:
        # ── Cache check ──────────────────────────────────────────
        tier = cache.check(alert.signature)
        if tier is not None:
            logger.info(
                "Skipping alert %s — dedup cache hit on %s",
                alert.signature,
                tier,
            )
            return

        # ── Cache miss — investigate ─────────────────────────────
        from dark_factory.obelisk.investigator import investigate

        logger.info("Cache miss for %s — launching investigation", alert.signature)
        result = await investigate(
            alert,
            factory_workspace,
            user_workspace,
            repo=repo,
            dedup_cache=cache,
        )
        logger.info(
            "Investigation %s completed: verdict=%s url=%s",
            result.id,
            result.verdict,
            result.outcome_url,
        )

    return _on_alert
