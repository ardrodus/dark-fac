"""Human gate handling for interactive TUI and auto mode.

Provides mode-aware handling of human approval gates:

- **Interactive (TUI)**: gates queue to the dashboard via
  :class:`HumanGateQueue`, and the user sees a prompt with context
  and can approve or reject.
- **Auto mode**: gates add a comment and label to the GitHub issue
  so a human can review asynchronously.

Three gate types are recognised:

- ``NEEDS_HUMAN`` -- from architecture review (engineering brief context).
- ``NEEDS_LIVE``  -- from Crucible (test results context).
- ``ESCALATION``  -- from code review after 3 rounds (review history).

The two :class:`Interviewer` implementations (:class:`TuiInterviewer`
and :class:`AutoModeInterviewer`) are drop-in replacements for the
existing interviewers in :mod:`factory.engine.handlers.human`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dark_factory.engine.handlers.human import Answer, Question

logger = logging.getLogger(__name__)

# ── Gate types ──────────────────────────────────────────────────────


class HumanGateType(StrEnum):
    """Type of human gate encountered during pipeline execution."""

    NEEDS_HUMAN = "needs_human"
    NEEDS_LIVE = "needs_live"
    ESCALATION = "escalation"


# ── Data models ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HumanGateRequest:
    """A pending human gate request with contextual information."""

    gate_type: HumanGateType
    issue_number: int
    title: str
    context: str
    stage: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HumanGateResponse:
    """Response to a human gate: approved or rejected with optional comment."""

    approved: bool
    comment: str = ""


# ── Labels ──────────────────────────────────────────────────────────

LABEL_NEEDS_HUMAN = "factory:needs-human"
LABEL_NEEDS_LIVE = "factory:needs-live"
LABEL_ESCALATION = "factory:escalation"

_GATE_LABELS: dict[HumanGateType, str] = {
    HumanGateType.NEEDS_HUMAN: LABEL_NEEDS_HUMAN,
    HumanGateType.NEEDS_LIVE: LABEL_NEEDS_LIVE,
    HumanGateType.ESCALATION: LABEL_ESCALATION,
}

# ── Gate classification ─────────────────────────────────────────────

_NEEDS_HUMAN_NODES = frozenset({"arch_needs_human", "needs_human"})
_ESCALATION_NODES = frozenset({"escalate"})


def classify_gate(
    stage: str, metadata: dict[str, Any] | None = None,
) -> HumanGateType:
    """Determine the gate type from the pipeline node ID and metadata.

    The node IDs in the DOT pipelines map directly:

    - ``arch_needs_human``, ``needs_human`` -> NEEDS_HUMAN
    - ``escalate`` -> ESCALATION

    An explicit ``gate_type`` key in *metadata* takes precedence when
    the node ID does not match a known pattern.
    """
    if stage in _NEEDS_HUMAN_NODES:
        return HumanGateType.NEEDS_HUMAN
    if stage in _ESCALATION_NODES:
        return HumanGateType.ESCALATION
    if metadata:
        raw = metadata.get("gate_type", "")
        try:
            return HumanGateType(raw)
        except ValueError:
            pass
    return HumanGateType.NEEDS_HUMAN


# ── Comment builder ─────────────────────────────────────────────────


def build_gate_comment(
    gate_type: HumanGateType,
    question: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Build a markdown comment body for a GitHub issue.

    Each gate type produces a different template with the relevant
    context section (engineering brief, test results, or review history).
    """
    meta = metadata or {}

    if gate_type == HumanGateType.NEEDS_HUMAN:
        brief = meta.get("engineering_brief", "")
        body = (
            "**Dark Factory -- NEEDS_HUMAN gate**\n\n"
            "Architecture review requires human review.\n\n"
            f"**Question:** {question}\n"
        )
        if brief:
            body += f"\n**Engineering Brief:**\n\n{brief}\n"
        return body

    if gate_type == HumanGateType.NEEDS_LIVE:
        test_results = meta.get("test_results", "")
        body = (
            "**Dark Factory -- NEEDS_LIVE gate**\n\n"
            "Crucible tests require manual validation in a live environment.\n\n"
            f"**Details:** {question}\n"
        )
        if test_results:
            body += f"\n**Test Results:**\n\n{test_results}\n"
        return body

    # ESCALATION
    history = meta.get("review_history", "")
    rounds = meta.get("review_rounds", 3)
    body = (
        "**Dark Factory -- ESCALATION gate**\n\n"
        f"Code review escalated after {rounds} rounds.\n\n"
        f"**Details:** {question}\n"
    )
    if history:
        body += f"\n**Review History:**\n\n{history}\n"
    return body


# ── GitHub helper ───────────────────────────────────────────────────


def _comment_and_label(
    issue_number: int,
    body: str,
    label: str,
    *,
    repo: str = "",
    cwd: str | None = None,
) -> None:
    """Add a comment and label to a GitHub issue (best-effort)."""
    from dark_factory.integrations.gh_safe import (  # noqa: PLC0415
        GhSafeError,
        add_label,
        comment_on_issue,
    )

    try:
        comment_on_issue(issue_number, body, repo=repo or None, cwd=cwd)
    except GhSafeError:
        logger.exception("Failed to comment on #%d", issue_number)
    try:
        add_label(issue_number, label, repo=repo or None, cwd=cwd)
    except GhSafeError:
        logger.exception("Failed to add label '%s' to #%d", label, issue_number)


# ── TUI gate queue ──────────────────────────────────────────────────


class HumanGateQueue:
    """Async queue bridging the pipeline engine and the TUI dashboard.

    The pipeline submits a :class:`HumanGateRequest` via :meth:`submit`
    which blocks (``await``) until the TUI calls :meth:`respond` with
    the user's decision.  The dashboard reads :attr:`pending` during
    repaint to show waiting gates.
    """

    def __init__(self) -> None:
        self._pending: list[HumanGateRequest] = []
        self._events: dict[str, asyncio.Event] = {}
        self._responses: dict[str, HumanGateResponse] = {}

    @staticmethod
    def _key(req: HumanGateRequest) -> str:
        return f"{req.gate_type}:{req.issue_number}:{req.stage}"

    async def submit(self, request: HumanGateRequest) -> HumanGateResponse:
        """Submit a gate request and wait for the user's response."""
        key = self._key(request)
        event = asyncio.Event()
        self._events[key] = event
        self._pending.append(request)
        try:
            await event.wait()
            return self._responses.pop(
                key,
                HumanGateResponse(approved=False, comment="no response"),
            )
        finally:
            self._pending = [r for r in self._pending if r is not request]
            self._events.pop(key, None)

    def respond(self, request: HumanGateRequest, response: HumanGateResponse) -> None:
        """Resolve a pending gate with the user's decision."""
        key = self._key(request)
        self._responses[key] = response
        event = self._events.get(key)
        if event is not None:
            event.set()

    @property
    def pending(self) -> list[HumanGateRequest]:
        """Snapshot of all pending (unresolved) gate requests."""
        return list(self._pending)


# ── Interviewer: TUI mode ──────────────────────────────────────────


class TuiInterviewer:
    """Interviewer that queues human gates to the TUI dashboard.

    Satisfies the :class:`~factory.engine.handlers.human.Interviewer`
    protocol.  Each ``ask()`` call creates a :class:`HumanGateRequest`,
    submits it to the :class:`HumanGateQueue`, and blocks until the
    TUI user approves or rejects.
    """

    def __init__(
        self, gate_queue: HumanGateQueue, issue_number: int = 0,
    ) -> None:
        self._queue = gate_queue
        self._issue_number = issue_number

    async def ask(self, question: Question) -> Answer:
        from dark_factory.engine.handlers.human import Answer as _Answer  # noqa: PLC0415

        gate_type = classify_gate(question.stage, question.metadata)
        request = HumanGateRequest(
            gate_type=gate_type,
            issue_number=self._issue_number,
            title=question.text,
            context=question.metadata.get("context", question.text),
            stage=question.stage,
            metadata=question.metadata,
        )
        response = await self._queue.submit(request)
        value = "approved" if response.approved else "rejected"
        return _Answer(value=value, text=response.comment or value)

    async def ask_question(self, question: Question) -> Answer:
        return await self.ask(question)


# ── Interviewer: auto mode ─────────────────────────────────────────


class AutoModeInterviewer:
    """Interviewer that comments on GitHub issues for human gates.

    Satisfies the :class:`~factory.engine.handlers.human.Interviewer`
    protocol.  Each ``ask()`` call posts a contextual comment on the
    GitHub issue and adds the appropriate label, then returns
    ``"queued"`` so the pipeline can proceed (or halt).
    """

    def __init__(
        self,
        issue_number: int,
        *,
        repo: str = "",
        cwd: str | None = None,
    ) -> None:
        self._issue_number = issue_number
        self._repo = repo
        self._cwd = cwd

    async def ask(self, question: Question) -> Answer:
        from dark_factory.engine.handlers.human import Answer as _Answer  # noqa: PLC0415

        gate_type = classify_gate(question.stage, question.metadata)
        label = _GATE_LABELS[gate_type]
        body = build_gate_comment(gate_type, question.text, question.metadata)
        _comment_and_label(
            self._issue_number,
            body,
            label,
            repo=self._repo,
            cwd=self._cwd,
        )
        return _Answer(
            value="queued",
            text=f"Queued for human review ({gate_type})",
        )

    async def ask_question(self, question: Question) -> Answer:
        return await self.ask(question)


# ── NEEDS_LIVE helpers ──────────────────────────────────────────────


def make_needs_live_request(
    issue_number: int,
    test_results: str,
) -> HumanGateRequest:
    """Create a NEEDS_LIVE gate request for TUI dashboard consumption."""
    return HumanGateRequest(
        gate_type=HumanGateType.NEEDS_LIVE,
        issue_number=issue_number,
        title="Manual validation required",
        context=test_results,
        stage="crucible",
        metadata={"test_results": test_results},
    )


def handle_needs_live_auto(
    issue_number: int,
    test_results: str,
    *,
    repo: str = "",
    cwd: str | None = None,
) -> None:
    """Handle NEEDS_LIVE verdict in auto mode: comment + label on GitHub."""
    body = build_gate_comment(
        HumanGateType.NEEDS_LIVE,
        "Crucible tests require manual validation",
        {"test_results": test_results},
    )
    _comment_and_label(
        issue_number,
        body,
        LABEL_NEEDS_LIVE,
        repo=repo,
        cwd=cwd,
    )
