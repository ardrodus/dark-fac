"""Issue dispatch and queue management.

Orchestrates the selection, labelling, and dispatching of GitHub issues
through the Dark Factory pipeline.  Interacts with the GitHub CLI via
:mod:`factory.integrations.gh_safe` and labels failed issues as
``factory:failed``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dark_factory.integrations.gh_safe import (
    GhSafeError,
    IssueInfo,
    add_label,
    list_issues,
    remove_label,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# ── Label constants ───────────────────────────────────────────────────

LABEL_QUEUED = "factory:queued"
LABEL_IN_PROGRESS = "factory:in-progress"
LABEL_DONE = "factory:done"
LABEL_FAILED = "factory:failed"

# ── Timing ────────────────────────────────────────────────────────────

_DEFAULT_POLL_INTERVAL = 30.0
_DEFAULT_MAX_CONCURRENT = 3


# ── Data types ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Outcome of dispatching a single issue."""

    issue_number: int
    success: bool
    message: str


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


# ── Label management ──────────────────────────────────────────────────


def _apply_label_transition(
    issue_number: int,
    *,
    remove: str,
    add: str,
    repo: str | None = None,
    cwd: str | None = None,
) -> None:
    """Atomically swap one label for another on an issue."""
    try:
        remove_label(issue_number, remove, repo=repo, cwd=cwd)
    except GhSafeError:
        logger.warning("Could not remove label '%s' from #%d (may already be absent)", remove, issue_number)
    add_label(issue_number, add, repo=repo, cwd=cwd)


# ── Concurrency control ──────────────────────────────────────────────


def _acquire_slot(issue_number: int, state: DispatcherState) -> bool:
    """Try to claim a dispatch slot for *issue_number*.

    Returns ``True`` if the slot was acquired, ``False`` if at capacity
    or the issue is already active.
    """
    with state.lock:
        if len(state.active_issues) >= state.max_concurrent:
            return False
        if issue_number in state.active_issues:
            return False
        state.active_issues.add(issue_number)
        return True


def _release_slot(issue_number: int, state: DispatcherState) -> None:
    """Release the dispatch slot held by *issue_number*."""
    with state.lock:
        state.active_issues.discard(issue_number)


# ── Core dispatch ─────────────────────────────────────────────────────


def dispatch_issue(
    issue: IssueInfo,
    *,
    handler: Callable[[IssueInfo], bool] | None = None,
    state: DispatcherState | None = None,
    repo: str | None = None,
    cwd: str | None = None,
) -> DispatchResult:
    """Dispatch a single issue through the pipeline.

    Parameters
    ----------
    issue:
        The issue to dispatch.
    handler:
        Callable that processes the issue; returns ``True`` on success.
        When ``None``, the dispatch is recorded but no processing occurs
        (useful for dry-run / testing).
    state:
        Shared dispatcher state for concurrency control.
    repo:
        GitHub repository slug (``owner/repo``).
    cwd:
        Working directory for ``gh`` calls.
    """
    ds = state or DispatcherState()

    if not _acquire_slot(issue.number, ds):
        return DispatchResult(
            issue_number=issue.number,
            success=False,
            message="concurrency limit reached or issue already active",
        )

    try:
        _apply_label_transition(issue.number, remove=LABEL_QUEUED, add=LABEL_IN_PROGRESS, repo=repo, cwd=cwd)

        success = handler(issue) if handler is not None else True

        if success:
            _apply_label_transition(
                issue.number, remove=LABEL_IN_PROGRESS, add=LABEL_DONE, repo=repo, cwd=cwd
            )
            return DispatchResult(issue_number=issue.number, success=True, message="dispatched successfully")

        _apply_label_transition(
            issue.number, remove=LABEL_IN_PROGRESS, add=LABEL_FAILED, repo=repo, cwd=cwd
        )
        logger.warning("Dispatch handler returned failure for #%d", issue.number)
        return DispatchResult(issue_number=issue.number, success=False, message="handler returned failure")

    except Exception:
        logger.exception("Unhandled error dispatching #%d", issue.number)
        try:
            _apply_label_transition(
                issue.number, remove=LABEL_IN_PROGRESS, add=LABEL_FAILED, repo=repo, cwd=cwd
            )
        except GhSafeError:
            logger.warning("Could not apply failure label to #%d", issue.number)
        logger.warning("Unhandled exception dispatching #%d, labeled as failed", issue.number)
        return DispatchResult(issue_number=issue.number, success=False, message="unhandled exception")
    finally:
        _release_slot(issue.number, ds)


# ── Queue writes ──────────────────────────────────────────────────────


def enqueue_issue(issue_number: int, *, repo: str | None = None, cwd: str | None = None) -> None:
    """Mark an issue as queued for dispatch by adding the queued label."""
    add_label(issue_number, LABEL_QUEUED, repo=repo, cwd=cwd)


# ── Auto-mode loop ───────────────────────────────────────────────────


def auto_main_loop(
    *,
    handler: Callable[[IssueInfo], bool] | None = None,
    state: DispatcherState | None = None,
    repo: str | None = None,
    cwd: str | None = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    max_iterations: int | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> list[DispatchResult]:
    """Continuously poll for queued issues and dispatch them.

    Parameters
    ----------
    max_iterations:
        Stop after this many poll cycles (``None`` = run forever).
    sleep_fn:
        Override for ``time.sleep`` (useful for testing).
    """
    ds = state or DispatcherState()
    _sleep = sleep_fn or time.sleep
    results: list[DispatchResult] = []
    iteration = 0

    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        logger.debug("auto_main_loop: poll iteration %d", iteration)

        issue = select_next_issue(repo=repo, cwd=cwd, state=ds)
        if issue is None:
            _sleep(poll_interval)
            continue

        result = dispatch_issue(issue, handler=handler, state=ds, repo=repo, cwd=cwd)
        results.append(result)

        if not result.success:
            _sleep(poll_interval)

    return results
