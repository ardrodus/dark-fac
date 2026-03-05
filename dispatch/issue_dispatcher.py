"""Issue dispatch and queue management.

Provides label constants and issue selection for the Dark Factory
pipeline.  Interacts with the GitHub CLI via
:mod:`factory.integrations.gh_safe`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from dark_factory.integrations.gh_safe import (
    GhSafeError,
    IssueInfo,
    list_issues,
)

logger = logging.getLogger(__name__)

# ── Label constants ───────────────────────────────────────────────────

LABEL_QUEUED = "factory-task"
LABEL_IN_PROGRESS = "in-progress"
LABEL_DONE = "done"
LABEL_FAILED = "human-review"

# ── Data types ────────────────────────────────────────────────────────

_DEFAULT_MAX_CONCURRENT = 3


@dataclass(slots=True)
class DispatcherState:
    """Mutable state for the dispatcher's concurrency tracking."""

    active_issues: set[int] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)
    max_concurrent: int = _DEFAULT_MAX_CONCURRENT
    dev_mode: bool = False


# ── Issue selection ───────────────────────────────────────────────────


def select_next_issue(
    *,
    repo: str | None = None,
    cwd: str | None = None,
    state: DispatcherState | None = None,
) -> IssueInfo | None:
    """Pick the next queued issue to dispatch.

    Returns the oldest queued issue that is not already being processed,
    or ``None`` when the queue is empty or concurrency is at capacity.
    """
    ds = state or DispatcherState()
    with ds.lock:
        if len(ds.active_issues) >= ds.max_concurrent:
            logger.debug("Concurrency limit reached (%d/%d)", len(ds.active_issues), ds.max_concurrent)
            return None

    try:
        issues = list_issues(labels=[LABEL_QUEUED], repo=repo, cwd=cwd)
    except GhSafeError:
        logger.exception("Failed to list queued issues")
        return None

    with ds.lock:
        for issue in issues:
            if issue.number not in ds.active_issues:
                return issue
    return None
