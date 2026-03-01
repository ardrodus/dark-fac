"""Dead-letter queue — pure queue management.

Scoped to enqueue / dequeue / peek / drain / stats.
No retry logic (dispatcher's job via resilience.py).
No diagnostic or issue-filing logic (Obelisk's job).
Queue backed by filesystem JSON-lines files.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DLQ_DIR = Path(".dark-factory")
_DLQ_FILENAME = "dead_letter_queue.jsonl"


@dataclass(frozen=True, slots=True)
class DLQEntry:
    """A single dead-letter queue record."""

    issue_number: int
    reason: str
    timestamp: float = field(default_factory=time.time)
    labels: tuple[str, ...] = ()
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class DLQStats:
    """Queue statistics snapshot."""

    depth: int
    oldest_timestamp: float | None
    newest_timestamp: float | None


# ── Internal helpers ─────────────────────────────────────────────────


def _resolve_path(dlq_dir: Path | None = None) -> Path:
    """Return the DLQ file path, creating the directory if needed."""
    directory = dlq_dir or _DEFAULT_DLQ_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory / _DLQ_FILENAME


def _parse_entry(data: dict[str, object]) -> DLQEntry:
    """Deserialise a single JSON object into a DLQEntry."""
    raw_labels = data.get("labels", [])
    label_tuple = tuple(raw_labels) if isinstance(raw_labels, list) else ()
    raw_number = data.get("issue_number", 0)
    raw_timestamp = data.get("timestamp", 0.0)
    raw_attempt = data.get("attempt", 1)
    return DLQEntry(
        issue_number=int(raw_number) if isinstance(raw_number, (int, float)) else 0,
        reason=str(data.get("reason", "")),
        timestamp=float(raw_timestamp) if isinstance(raw_timestamp, (int, float)) else 0.0,
        labels=tuple(str(lb) for lb in label_tuple),
        attempt=int(raw_attempt) if isinstance(raw_attempt, (int, float)) else 1,
    )


def _read_all(path: Path) -> list[DLQEntry]:
    """Read every entry from the JSONL file."""
    if not path.exists():
        return []
    entries: list[DLQEntry] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            data: dict[str, object] = json.loads(stripped)
            entries.append(_parse_entry(data))
    return entries


def _write_all(path: Path, entries: list[DLQEntry]) -> None:
    """Overwrite the JSONL file with *entries*."""
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            data = asdict(entry)
            data["labels"] = list(data["labels"])
            fh.write(json.dumps(data, separators=(",", ":")) + "\n")


def _serialise_entry(entry: DLQEntry) -> str:
    """Convert a DLQEntry to a compact JSON string."""
    data = asdict(entry)
    data["labels"] = list(data["labels"])
    return json.dumps(data, separators=(",", ":"))


# ── Public API ───────────────────────────────────────────────────────


def enqueue(entry: DLQEntry, *, dlq_dir: Path | None = None) -> Path:
    """Append *entry* to the dead-letter queue.

    Returns the path to the DLQ file.
    """
    path = _resolve_path(dlq_dir)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_serialise_entry(entry) + "\n")
    logger.info("DLQ enqueue: issue #%d — %s", entry.issue_number, entry.reason)
    return path


def dequeue(*, dlq_dir: Path | None = None) -> DLQEntry | None:
    """Remove and return the oldest entry, or ``None`` if the queue is empty.

    The caller (dispatcher) decides whether to retry or escalate to Obelisk.
    """
    path = _resolve_path(dlq_dir)
    entries = _read_all(path)
    if not entries:
        return None
    oldest = entries[0]
    _write_all(path, entries[1:])
    logger.info("DLQ dequeue: issue #%d", oldest.issue_number)
    return oldest


def peek(*, dlq_dir: Path | None = None) -> DLQEntry | None:
    """Return the oldest entry without removing it, or ``None`` if empty."""
    path = _resolve_path(dlq_dir)
    entries = _read_all(path)
    return entries[0] if entries else None


def drain(*, dlq_dir: Path | None = None) -> list[DLQEntry]:
    """Remove and return all entries from the queue."""
    path = _resolve_path(dlq_dir)
    entries = _read_all(path)
    if entries:
        _write_all(path, [])
    return entries


def stats(*, dlq_dir: Path | None = None) -> DLQStats:
    """Return a snapshot of queue statistics."""
    path = _resolve_path(dlq_dir)
    entries = _read_all(path)
    if not entries:
        return DLQStats(depth=0, oldest_timestamp=None, newest_timestamp=None)
    timestamps = [e.timestamp for e in entries]
    return DLQStats(
        depth=len(entries),
        oldest_timestamp=min(timestamps),
        newest_timestamp=max(timestamps),
    )
