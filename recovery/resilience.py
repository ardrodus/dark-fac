"""Resilience primitives — retry, circuit breaker, timeout.

Scoped to three composable functions.  Does NOT classify failures
(dispatcher's job) or file issues / run diagnostics (Obelisk's job).
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from tenacity import RetryError, Retrying, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_STATE_DIR = Path(".dark-factory")
_CB_FILENAME = "circuit-breaker.json"


# ── Errors ──────────────────────────────────────────────────────────


class RetriesExhaustedError(Exception):
    """All retry attempts have been exhausted."""

    def __init__(
        self, context: str, attempts: int, last_exception: BaseException | None = None,
    ) -> None:
        self.context = context
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(f"{context}: all {attempts} retries exhausted")


class CircuitOpenError(Exception):
    """Circuit breaker is open — calls are rejected."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"circuit breaker '{name}' is open")


class TimeoutExpiredError(Exception):
    """Callable exceeded its timeout."""

    def __init__(self, context: str, timeout_seconds: float) -> None:
        self.context = context
        self.timeout_seconds = timeout_seconds
        super().__init__(f"{context}: timed out after {timeout_seconds}s")


# ── Circuit-breaker state helpers ───────────────────────────────────


class CircuitState(Enum):
    """Possible states for a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class _CBRecord:
    state: CircuitState
    failure_count: int
    last_failure_time: float
    last_success_time: float


_DEFAULT_RECORD = _CBRecord(CircuitState.CLOSED, 0, 0.0, 0.0)


def _load_cb_state(name: str, state_dir: Path) -> _CBRecord:
    path = state_dir / _CB_FILENAME
    if not path.exists():
        return _DEFAULT_RECORD
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _DEFAULT_RECORD
    if not isinstance(raw, dict):
        return _DEFAULT_RECORD
    record: object = raw.get(name)
    if not isinstance(record, dict):
        return _DEFAULT_RECORD
    raw_fc = record.get("failure_count", 0)
    raw_lft = record.get("last_failure_time", 0.0)
    raw_lst = record.get("last_success_time", 0.0)
    return _CBRecord(
        state=CircuitState(str(record.get("state", "closed"))),
        failure_count=int(raw_fc) if isinstance(raw_fc, (int, float)) else 0,
        last_failure_time=float(raw_lft) if isinstance(raw_lft, (int, float)) else 0.0,
        last_success_time=float(raw_lst) if isinstance(raw_lst, (int, float)) else 0.0,
    )


def _save_cb_state(name: str, record: _CBRecord, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _CB_FILENAME
    data: dict[str, object] = {}
    if path.exists():
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): v for k, v in raw.items()}
        except (json.JSONDecodeError, OSError):
            pass
    data[name] = {
        "state": record.state.value,
        "failure_count": record.failure_count,
        "last_failure_time": record.last_failure_time,
        "last_success_time": record.last_success_time,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Public API ──────────────────────────────────────────────────────


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    context: str = "retry",
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """Execute *fn* with exponential backoff.

    Raises :class:`RetriesExhaustedError` when all attempts fail.
    The recovery dispatcher catches this and routes to the DLQ.
    """
    retryer = Retrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=base_delay, max=max_delay),
        sleep=sleep_fn,
    )
    try:
        return retryer(fn)
    except RetryError as exc:
        last = exc.last_attempt.exception() if exc.last_attempt else None
        raise RetriesExhaustedError(context, max_attempts, last) from exc


def circuit_breaker(
    fn: Callable[[], T],
    *,
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    state_dir: Path | None = None,
    time_fn: Callable[[], float] = time.time,
) -> T:
    """Execute *fn* through a named circuit breaker.

    State is persisted to ``<state_dir>/circuit-breaker.json``.
    Raises :class:`CircuitOpenError` when the circuit is open.
    """
    directory = state_dir or _DEFAULT_STATE_DIR
    record = _load_cb_state(name, directory)
    now = time_fn()

    if record.state == CircuitState.OPEN:
        if now - record.last_failure_time >= recovery_timeout:
            record = _CBRecord(
                CircuitState.HALF_OPEN, record.failure_count,
                record.last_failure_time, record.last_success_time,
            )
            _save_cb_state(name, record, directory)
            logger.info("circuit '%s' -> HALF_OPEN", name)
        else:
            raise CircuitOpenError(name)

    try:
        result = fn()
    except Exception:
        new_count = record.failure_count + 1
        new_state = CircuitState.OPEN if new_count >= failure_threshold else CircuitState.CLOSED
        if new_state == CircuitState.OPEN:
            logger.warning("circuit '%s' -> OPEN (failures=%d)", name, new_count)
        _save_cb_state(
            name, _CBRecord(new_state, new_count, now, record.last_success_time), directory,
        )
        raise

    _save_cb_state(
        name, _CBRecord(CircuitState.CLOSED, 0, record.last_failure_time, now), directory,
    )
    if record.state == CircuitState.HALF_OPEN:
        logger.info("circuit '%s' -> CLOSED (recovered)", name)
    return result


def timeout_wrapper(
    fn: Callable[[], T],
    *,
    context: str = "timeout",
    timeout_seconds: float = 30.0,
) -> T:
    """Execute *fn* with a wall-clock timeout.

    Raises :class:`TimeoutExpiredError` if *fn* does not complete
    within *timeout_seconds*.  Uses a thread pool for cross-platform
    compatibility (Windows + Unix).
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future: concurrent.futures.Future[T] = pool.submit(fn)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutExpiredError(context, timeout_seconds) from None
