"""Parallel story execution with dependency awareness (port of parallel-stories.sh)."""
from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
_DEFAULT_MAX = 3


class StoryStatus(Enum):
    """Execution status of a story in the parallel pipeline."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class Story:
    """A story to execute, with optional dependency chain."""

    id: str
    title: str = ""
    description: str = ""
    acceptance_criteria: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoryResult:
    """Outcome of a single story execution."""

    story_id: str
    status: StoryStatus
    duration_seconds: float = 0.0
    error: str = ""


@dataclass(frozen=True, slots=True)
class ParallelConfig:
    """Configuration for parallel execution."""

    cwd: str | None = None
    run_fn: Callable[[Story], bool] | None = None


ProgressCallback = Callable[[str, StoryStatus, dict[str, StoryStatus]], None]


# ── Dependency graph helpers ─────────────────────────────────────


def _resolve_deps(stories: list[Story]) -> dict[str, set[str]]:
    """Build dependency map, filtering to known story IDs only."""
    known = {s.id for s in stories}
    return {s.id: {d for d in s.depends_on if d in known} for s in stories}


def _find_ready(deps: dict[str, set[str]], st: dict[str, StoryStatus]) -> list[str]:
    """Return IDs of stories whose dependencies are all COMPLETED."""
    return [
        sid
        for sid, d in deps.items()
        if st[sid] == StoryStatus.PENDING
        and all(st.get(x) == StoryStatus.COMPLETED for x in d)
    ]


def _mark_blocked(
    failed_id: str,
    deps: dict[str, set[str]],
    st: dict[str, StoryStatus],
) -> list[str]:
    """Transitively mark dependents of a failed story as BLOCKED."""
    blocked: list[str] = []
    queue = [s for s, d in deps.items() if failed_id in d and st[s] == StoryStatus.PENDING]
    while queue:
        cur = queue.pop(0)
        if st[cur] == StoryStatus.PENDING:
            st[cur] = StoryStatus.BLOCKED
            blocked.append(cur)
            queue.extend(
                s for s, d in deps.items() if cur in d and st[s] == StoryStatus.PENDING
            )
    return blocked


# ── Story runner ─────────────────────────────────────────────────


def _run_story(story: Story, config: ParallelConfig) -> bool:
    """Execute a single story, returning True on success."""
    if config.run_fn is not None:
        return config.run_fn(story)
    from factory.pipeline.runner import StoryContext, run_pipeline  # noqa: PLC0415

    ctx = StoryContext(
        title=story.title,
        description=story.description,
        acceptance_criteria=story.acceptance_criteria,
    )
    return run_pipeline(ctx, cwd=config.cwd).passed


# ── Public API ───────────────────────────────────────────────────


def execute_parallel(
    stories: list[Story],
    config: ParallelConfig,
    max_concurrent: int = _DEFAULT_MAX,
    *,
    progress_cb: ProgressCallback | None = None,
) -> list[StoryResult]:
    """Execute stories concurrently, respecting dependsOn chains.

    Stories whose dependencies have all completed are submitted to a thread
    pool (up to *max_concurrent* at a time).  When a story fails, all
    transitive dependents are marked BLOCKED.  Circular dependencies are
    detected and reported as BLOCKED at the end.
    """
    if not stories:
        return []

    smap = {s.id: s for s in stories}
    deps = _resolve_deps(stories)
    st: dict[str, StoryStatus] = {s.id: StoryStatus.PENDING for s in stories}
    results: dict[str, StoryResult] = {}
    t0s: dict[str, float] = {}
    mc = max(1, max_concurrent)

    def _notify(sid: str, status: StoryStatus) -> None:
        st[sid] = status
        if progress_cb:
            progress_cb(sid, status, dict(st))

    def _fail(sid: str, err: str) -> None:
        elapsed = round(time.monotonic() - t0s.get(sid, time.monotonic()), 2)
        _notify(sid, StoryStatus.FAILED)
        results[sid] = StoryResult(sid, StoryStatus.FAILED, elapsed, err)
        for bid in _mark_blocked(sid, deps, st):
            results[bid] = StoryResult(
                bid, StoryStatus.BLOCKED, error=f"dependency {sid} failed"
            )
            if progress_cb:
                progress_cb(bid, StoryStatus.BLOCKED, dict(st))

    with ThreadPoolExecutor(max_workers=mc) as pool:
        futs: dict[Future[bool], str] = {}

        while True:
            # Submit newly ready stories up to concurrency cap
            for sid in _find_ready(deps, st):
                if len(futs) >= mc:
                    break
                t0s[sid] = time.monotonic()
                _notify(sid, StoryStatus.RUNNING)
                futs[pool.submit(_run_story, smap[sid], config)] = sid

            if not futs:
                break

            # Wait for at least one completion, then process results
            done, _ = wait(set(futs), return_when=FIRST_COMPLETED)
            for fut in done:
                sid = futs.pop(fut)
                try:
                    ok = fut.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Story %s raised", sid)
                    _fail(sid, str(exc))
                    continue
                if ok:
                    elapsed = round(time.monotonic() - t0s[sid], 2)
                    _notify(sid, StoryStatus.COMPLETED)
                    results[sid] = StoryResult(sid, StoryStatus.COMPLETED, elapsed)
                else:
                    _fail(sid, "run returned failure")

    # Any remaining PENDING stories have circular dependencies
    for sid in st:
        if st[sid] == StoryStatus.PENDING:
            st[sid] = StoryStatus.BLOCKED
            results[sid] = StoryResult(
                sid, StoryStatus.BLOCKED, error="circular dependency"
            )

    # Return results in the same order as the input stories
    return [results[s.id] for s in stories]
