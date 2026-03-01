"""Recovery dispatcher — single entry point for all pipeline failure recovery.

Hierarchy: retry → DLQ → Obelisk triage → escalate (issue with human-review).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from factory.integrations.shell import gh
from factory.recovery.dlq import DLQEntry, enqueue

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
_DEFAULT_LOG_DIR = Path(".dark-factory")
_LOG_FILENAME = "recovery-log.json"
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 2.0


class ErrorType(Enum):
    TRANSIENT = "transient"
    VALIDATION = "validation"
    INFRASTRUCTURE = "infrastructure"
    LOGIC = "logic"


class RecoveryLevel(Enum):
    RETRY = 1
    DLQ = 2
    DIAGNOSE = 3
    ESCALATE = 4


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    level: RecoveryLevel
    action: str
    result: str


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    context: str
    error_type: ErrorType
    level_reached: RecoveryLevel
    resolved: bool
    actions: tuple[RecoveryAction, ...]


@dataclass
class _RecoveryState:
    active: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _RecoveryState()


def _log_recovery(
    ctx: str, level: RecoveryLevel, action: str, result: str, *, log_dir: Path | None = None,
) -> None:
    directory = log_dir or _DEFAULT_LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": time.time(), "context": ctx, "level": level.name, "action": action, "result": result}
    with (directory / _LOG_FILENAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    logger.info("recovery [%s] %s: %s — %s", level.name, ctx, action, result)


def _attempt_retry(
    ctx: str, error_type: ErrorType, details: dict[str, object], *,
    log_dir: Path | None = None, max_retries: int = _MAX_RETRIES,
    retry_fn: Callable[[str, dict[str, object]], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    if error_type != ErrorType.TRANSIENT:
        _log_recovery(ctx, RecoveryLevel.RETRY, "skip", "non-transient error", log_dir=log_dir)
        return False
    for attempt in range(1, max_retries + 1):
        _log_recovery(ctx, RecoveryLevel.RETRY, f"retry-{attempt}", "attempting", log_dir=log_dir)
        if retry_fn is not None and retry_fn(ctx, details):
            _log_recovery(ctx, RecoveryLevel.RETRY, f"retry-{attempt}", "success", log_dir=log_dir)
            return True
        if attempt < max_retries:
            sleep_fn(_RETRY_DELAY_SECONDS * attempt)
    _log_recovery(ctx, RecoveryLevel.RETRY, "exhausted", "all retries failed", log_dir=log_dir)
    return False


def _attempt_dlq(
    ctx: str, details: dict[str, object], *, log_dir: Path | None = None, dlq_dir: Path | None = None,
) -> bool:
    raw_num = details.get("issue_number", 0)
    issue_number = int(raw_num) if isinstance(raw_num, (int, float)) else 0
    raw_reason = details.get("reason", ctx)
    reason = str(raw_reason)
    enqueue(DLQEntry(issue_number=issue_number, reason=reason), dlq_dir=dlq_dir)
    _log_recovery(ctx, RecoveryLevel.DLQ, "enqueue", f"issue #{issue_number}", log_dir=log_dir)
    return True


def _attempt_diagnose(
    ctx: str, details: dict[str, object], *, log_dir: Path | None = None,
    diagnose_fn: Callable[[str, dict[str, object]], bool] | None = None,
) -> bool:
    if diagnose_fn is not None:
        resolved = diagnose_fn(ctx, details)
        result = "resolved" if resolved else "unresolved"
        _log_recovery(ctx, RecoveryLevel.DIAGNOSE, "obelisk-triage", result, log_dir=log_dir)
        return resolved
    _log_recovery(ctx, RecoveryLevel.DIAGNOSE, "obelisk-triage", "unavailable", log_dir=log_dir)
    return False


def _attempt_escalate(
    ctx: str, error_type: ErrorType, details: dict[str, object], *,
    log_dir: Path | None = None, repo: str | None = None, cwd: str | None = None,
) -> bool:
    title = f"[recovery] {ctx}: {error_type.value} failure"
    body = json.dumps(details, indent=2, default=str)
    args = ["issue", "create", "--title", title, "--body", body, "--label", "human-review"]
    if repo:
        args.extend(["--repo", repo])
    try:
        result = gh(args, check=True, cwd=cwd)
        _log_recovery(ctx, RecoveryLevel.ESCALATE, "issue-created", result.stdout.strip() or "ok", log_dir=log_dir)
        return True
    except Exception:  # noqa: BLE001
        _log_recovery(ctx, RecoveryLevel.ESCALATE, "issue-create-failed", "gh error", log_dir=log_dir)
        return False


def handle_failure(
    context: str, error_type: ErrorType, details: dict[str, object], *,
    log_dir: Path | None = None, dlq_dir: Path | None = None,
    retry_fn: Callable[[str, dict[str, object]], bool] | None = None,
    diagnose_fn: Callable[[str, dict[str, object]], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_retries: int = _MAX_RETRIES, repo: str | None = None, cwd: str | None = None,
    state: _RecoveryState | None = None,
) -> RecoveryResult:
    """Handle a pipeline failure through the 4-level recovery hierarchy."""
    rs = state or _state
    with rs.lock:
        if context in rs.active:
            return RecoveryResult(
                context=context, error_type=error_type, level_reached=RecoveryLevel.RETRY,
                resolved=False, actions=(RecoveryAction(RecoveryLevel.RETRY, "rejected", "concurrent recovery"),),
            )
        rs.active.add(context)
    try:
        actions: list[RecoveryAction] = []
        if _attempt_retry(
            context, error_type, details, log_dir=log_dir,
            max_retries=max_retries, retry_fn=retry_fn, sleep_fn=sleep_fn,
        ):
            actions.append(RecoveryAction(RecoveryLevel.RETRY, "retry", "resolved"))
            return RecoveryResult(
                context=context, error_type=error_type, level_reached=RecoveryLevel.RETRY,
                resolved=True, actions=tuple(actions),
            )
        actions.append(RecoveryAction(RecoveryLevel.RETRY, "retry", "exhausted"))
        _attempt_dlq(context, details, log_dir=log_dir, dlq_dir=dlq_dir)
        actions.append(RecoveryAction(RecoveryLevel.DLQ, "enqueue", "queued"))
        if _attempt_diagnose(context, details, log_dir=log_dir, diagnose_fn=diagnose_fn):
            actions.append(RecoveryAction(RecoveryLevel.DIAGNOSE, "obelisk", "resolved"))
            return RecoveryResult(
                context=context, error_type=error_type, level_reached=RecoveryLevel.DIAGNOSE,
                resolved=True, actions=tuple(actions),
            )
        actions.append(RecoveryAction(RecoveryLevel.DIAGNOSE, "obelisk", "unresolved"))
        escalated = _attempt_escalate(context, error_type, details, log_dir=log_dir, repo=repo, cwd=cwd)
        actions.append(RecoveryAction(
            RecoveryLevel.ESCALATE, "issue-filed", "escalated" if escalated else "escalation-failed",
        ))
        return RecoveryResult(
            context=context, error_type=error_type, level_reached=RecoveryLevel.ESCALATE,
            resolved=escalated, actions=tuple(actions),
        )
    finally:
        with rs.lock:
            rs.active.discard(context)
