"""Global resource limiter for subprocess concurrency.

Prevents unbounded subprocess spawning (e.g. 30+ concurrent
``claude --print`` processes) by gating behind a semaphore.
"""

from __future__ import annotations

import asyncio
import threading


class ResourceLimiter:
    """Limits concurrent subprocess execution across the pipeline.

    Provides both an async semaphore (for the asyncio pipeline path)
    and a threading semaphore (for ThreadPoolExecutor paths like
    arch_review).

    The async semaphore is created lazily because it must be
    instantiated inside a running event loop.
    """

    def __init__(self, limit: int = 3) -> None:
        self._limit = max(1, limit)
        self._thread_semaphore = threading.Semaphore(self._limit)
        self._async_semaphore: asyncio.Semaphore | None = None

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def thread_semaphore(self) -> threading.Semaphore:
        return self._thread_semaphore

    def get_async_semaphore(self) -> asyncio.Semaphore:
        """Return the async semaphore, creating it lazily."""
        if self._async_semaphore is None:
            self._async_semaphore = asyncio.Semaphore(self._limit)
        return self._async_semaphore
