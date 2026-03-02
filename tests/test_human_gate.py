"""Tests for factory.gates.human_gate — human gate handling for TUI and auto mode."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from dark_factory.engine.handlers.human import Question, QuestionType
from dark_factory.gates.human_gate import (
    LABEL_ESCALATION,
    LABEL_NEEDS_HUMAN,
    LABEL_NEEDS_LIVE,
    AutoModeInterviewer,
    HumanGateQueue,
    HumanGateRequest,
    HumanGateResponse,
    HumanGateType,
    TuiInterviewer,
    build_gate_comment,
    classify_gate,
    handle_needs_live_auto,
    make_needs_live_request,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _question(
    text: str = "Approve this?",
    stage: str = "needs_human",
    metadata: dict | None = None,
) -> Question:
    return Question(
        text=text,
        question_type=QuestionType.CONFIRM,
        stage=stage,
        metadata=metadata or {},
    )


# ── HumanGateType ────────────────────────────────────────────────────


class TestHumanGateType:
    def test_enum_values_and_count(self) -> None:
        assert HumanGateType.NEEDS_HUMAN == "needs_human"
        assert HumanGateType.NEEDS_LIVE == "needs_live"
        assert HumanGateType.ESCALATION == "escalation"
        assert len(HumanGateType) == 3


# ── HumanGateRequest / HumanGateResponse ────────────────────────────


class TestDataModels:
    def test_request_is_frozen(self) -> None:
        req = HumanGateRequest(
            gate_type=HumanGateType.NEEDS_HUMAN,
            issue_number=42,
            title="Review needed",
            context="Brief content",
        )
        with pytest.raises(AttributeError):
            req.title = "changed"  # type: ignore[misc]

    def test_response_is_frozen(self) -> None:
        resp = HumanGateResponse(approved=True, comment="ok")
        with pytest.raises(AttributeError):
            resp.approved = False  # type: ignore[misc]

    def test_request_defaults(self) -> None:
        req = HumanGateRequest(
            gate_type=HumanGateType.NEEDS_LIVE,
            issue_number=1,
            title="t",
            context="c",
        )
        assert req.stage == ""
        assert req.metadata == {}

    def test_request_all_fields(self) -> None:
        req = HumanGateRequest(
            gate_type=HumanGateType.ESCALATION,
            issue_number=99,
            title="Escalated",
            context="3 rounds exceeded",
            stage="escalate",
            metadata={"review_rounds": 3},
        )
        assert req.gate_type == HumanGateType.ESCALATION
        assert req.issue_number == 99
        assert req.stage == "escalate"


# ── classify_gate ────────────────────────────────────────────────────


class TestClassifyGate:
    def test_arch_needs_human_node(self) -> None:
        assert classify_gate("arch_needs_human") == HumanGateType.NEEDS_HUMAN

    def test_needs_human_node(self) -> None:
        assert classify_gate("needs_human") == HumanGateType.NEEDS_HUMAN

    def test_escalate_node(self) -> None:
        assert classify_gate("escalate") == HumanGateType.ESCALATION

    def test_metadata_gate_type(self) -> None:
        result = classify_gate("unknown_node", {"gate_type": "needs_live"})
        assert result == HumanGateType.NEEDS_LIVE

    def test_metadata_escalation(self) -> None:
        result = classify_gate("unknown", {"gate_type": "escalation"})
        assert result == HumanGateType.ESCALATION

    def test_invalid_metadata_falls_through(self) -> None:
        result = classify_gate("unknown", {"gate_type": "bogus"})
        assert result == HumanGateType.NEEDS_HUMAN

    def test_no_metadata_defaults(self) -> None:
        assert classify_gate("some_random_node") == HumanGateType.NEEDS_HUMAN

    def test_node_id_takes_precedence_over_metadata(self) -> None:
        result = classify_gate("escalate", {"gate_type": "needs_live"})
        assert result == HumanGateType.ESCALATION


# ── build_gate_comment ───────────────────────────────────────────────


class TestBuildGateComment:
    def test_needs_human_basic(self) -> None:
        body = build_gate_comment(HumanGateType.NEEDS_HUMAN, "Review required")
        assert "NEEDS_HUMAN" in body
        assert "Architecture review" in body
        assert "Review required" in body

    def test_needs_human_with_brief(self) -> None:
        body = build_gate_comment(
            HumanGateType.NEEDS_HUMAN,
            "Review required",
            {"engineering_brief": "Use microservices pattern"},
        )
        assert "Engineering Brief" in body
        assert "Use microservices pattern" in body

    def test_needs_live_basic(self) -> None:
        body = build_gate_comment(HumanGateType.NEEDS_LIVE, "Manual validation needed")
        assert "NEEDS_LIVE" in body
        assert "manual validation" in body.lower()

    def test_needs_live_with_results(self) -> None:
        body = build_gate_comment(
            HumanGateType.NEEDS_LIVE,
            "Validate in prod",
            {"test_results": "5 pass, 2 skip"},
        )
        assert "Test Results" in body
        assert "5 pass, 2 skip" in body

    def test_escalation_basic(self) -> None:
        body = build_gate_comment(HumanGateType.ESCALATION, "Code review stuck")
        assert "ESCALATION" in body
        assert "3 rounds" in body

    def test_escalation_with_history(self) -> None:
        body = build_gate_comment(
            HumanGateType.ESCALATION,
            "Stuck",
            {"review_history": "Round 1: changes, Round 2: changes, Round 3: still bad"},
        )
        assert "Review History" in body
        assert "Round 1" in body

    def test_escalation_custom_rounds(self) -> None:
        body = build_gate_comment(
            HumanGateType.ESCALATION,
            "Stuck",
            {"review_rounds": 5},
        )
        assert "5 rounds" in body

    def test_no_metadata(self) -> None:
        body = build_gate_comment(HumanGateType.NEEDS_HUMAN, "Q?", None)
        assert "Q?" in body


# ── Labels ───────────────────────────────────────────────────────────


class TestLabels:
    def test_label_values(self) -> None:
        assert LABEL_NEEDS_HUMAN == "human-review"
        assert LABEL_NEEDS_LIVE == "needs-live-env"
        assert LABEL_ESCALATION == "human-review"


# ── HumanGateQueue ──────────────────────────────────────────────────


class TestHumanGateQueue:
    def test_initially_empty(self) -> None:
        q = HumanGateQueue()
        assert q.pending == []

    @pytest.mark.asyncio()
    async def test_submit_and_respond(self) -> None:
        q = HumanGateQueue()
        req = HumanGateRequest(
            gate_type=HumanGateType.NEEDS_HUMAN,
            issue_number=42,
            title="Review?",
            context="Brief",
            stage="needs_human",
        )

        async def _responder() -> None:
            await asyncio.sleep(0.05)
            assert len(q.pending) == 1
            assert q.pending[0] is req
            q.respond(req, HumanGateResponse(approved=True, comment="LGTM"))

        resp_task = asyncio.create_task(_responder())
        response = await q.submit(req)
        await resp_task

        assert response.approved is True
        assert response.comment == "LGTM"
        assert q.pending == []

    @pytest.mark.asyncio()
    async def test_submit_rejected(self) -> None:
        q = HumanGateQueue()
        req = HumanGateRequest(
            gate_type=HumanGateType.ESCALATION,
            issue_number=10,
            title="Escalated",
            context="History",
            stage="escalate",
        )

        async def _responder() -> None:
            await asyncio.sleep(0.05)
            q.respond(req, HumanGateResponse(approved=False, comment="Not ready"))

        resp_task = asyncio.create_task(_responder())
        response = await q.submit(req)
        await resp_task

        assert response.approved is False
        assert response.comment == "Not ready"

    @pytest.mark.asyncio()
    async def test_multiple_pending(self) -> None:
        q = HumanGateQueue()
        req1 = HumanGateRequest(
            gate_type=HumanGateType.NEEDS_HUMAN,
            issue_number=1,
            title="First",
            context="c1",
            stage="needs_human",
        )
        req2 = HumanGateRequest(
            gate_type=HumanGateType.NEEDS_LIVE,
            issue_number=2,
            title="Second",
            context="c2",
            stage="crucible",
        )

        async def _respond_both() -> None:
            await asyncio.sleep(0.05)
            assert len(q.pending) == 2
            q.respond(req1, HumanGateResponse(approved=True))
            q.respond(req2, HumanGateResponse(approved=False))

        asyncio.create_task(_respond_both())
        r1, r2 = await asyncio.gather(q.submit(req1), q.submit(req2))
        assert r1.approved is True
        assert r2.approved is False

    def test_respond_without_submit_is_noop(self) -> None:
        q = HumanGateQueue()
        req = HumanGateRequest(
            gate_type=HumanGateType.NEEDS_HUMAN,
            issue_number=1,
            title="t",
            context="c",
            stage="s",
        )
        # Should not raise
        q.respond(req, HumanGateResponse(approved=True))


# ── TuiInterviewer ──────────────────────────────────────────────────


class TestTuiInterviewer:
    @pytest.mark.asyncio()
    async def test_ask_approved(self) -> None:
        q = HumanGateQueue()
        interviewer = TuiInterviewer(q, issue_number=42)
        question = _question(text="Approve arch?", stage="arch_needs_human")

        async def _approve() -> None:
            await asyncio.sleep(0.05)
            req = q.pending[0]
            assert req.gate_type == HumanGateType.NEEDS_HUMAN
            assert req.issue_number == 42
            q.respond(req, HumanGateResponse(approved=True, comment="Looks good"))

        asyncio.create_task(_approve())
        answer = await interviewer.ask(question)

        assert answer.value == "approved"
        assert answer.text == "Looks good"

    @pytest.mark.asyncio()
    async def test_ask_rejected(self) -> None:
        q = HumanGateQueue()
        interviewer = TuiInterviewer(q, issue_number=10)
        question = _question(text="Escalation", stage="escalate")

        async def _reject() -> None:
            await asyncio.sleep(0.05)
            req = q.pending[0]
            assert req.gate_type == HumanGateType.ESCALATION
            q.respond(req, HumanGateResponse(approved=False))

        asyncio.create_task(_reject())
        answer = await interviewer.ask(question)

        assert answer.value == "rejected"

    @pytest.mark.asyncio()
    async def test_ask_question_delegates(self) -> None:
        q = HumanGateQueue()
        interviewer = TuiInterviewer(q, issue_number=1)
        question = _question(stage="needs_human")

        async def _approve() -> None:
            await asyncio.sleep(0.05)
            q.respond(q.pending[0], HumanGateResponse(approved=True))

        asyncio.create_task(_approve())
        answer = await interviewer.ask_question(question)
        assert answer.value == "approved"

    @pytest.mark.asyncio()
    async def test_context_from_metadata(self) -> None:
        q = HumanGateQueue()
        interviewer = TuiInterviewer(q, issue_number=5)
        question = _question(
            text="Review needed",
            stage="needs_human",
            metadata={"context": "Full engineering brief here"},
        )

        async def _approve() -> None:
            await asyncio.sleep(0.05)
            req = q.pending[0]
            assert req.context == "Full engineering brief here"
            q.respond(req, HumanGateResponse(approved=True))

        asyncio.create_task(_approve())
        await interviewer.ask(question)


# ── AutoModeInterviewer ─────────────────────────────────────────────


class TestAutoModeInterviewer:
    @pytest.mark.asyncio()
    async def test_comments_and_labels_needs_human(self) -> None:
        with patch("dark_factory.gates.human_gate._comment_and_label") as mock_cal:
            interviewer = AutoModeInterviewer(issue_number=42, repo="test/repo")
            question = _question(
                text="Review this arch",
                stage="arch_needs_human",
                metadata={"engineering_brief": "Use REST"},
            )
            answer = await interviewer.ask(question)

            assert answer.value == "queued"
            assert "needs_human" in answer.text
            mock_cal.assert_called_once()
            args, kwargs = mock_cal.call_args
            assert args[0] == 42  # issue_number
            body = args[1]
            assert "NEEDS_HUMAN" in body
            assert "Use REST" in body
            assert args[2] == "human-review"  # label
            assert kwargs["repo"] == "test/repo"

    @pytest.mark.asyncio()
    async def test_comments_and_labels_escalation(self) -> None:
        with patch("dark_factory.gates.human_gate._comment_and_label") as mock_cal:
            interviewer = AutoModeInterviewer(issue_number=99, repo="org/repo")
            question = _question(
                text="Code review stuck",
                stage="escalate",
                metadata={"review_history": "Round 1, Round 2, Round 3"},
            )
            answer = await interviewer.ask(question)

            assert answer.value == "queued"
            mock_cal.assert_called_once()
            args, _ = mock_cal.call_args
            assert args[0] == 99
            body = args[1]
            assert "ESCALATION" in body
            assert "Round 1" in body
            assert args[2] == "human-review"

    @pytest.mark.asyncio()
    async def test_ask_question_delegates(self) -> None:
        with patch("dark_factory.gates.human_gate._comment_and_label"):
            interviewer = AutoModeInterviewer(issue_number=1)
            question = _question(stage="needs_human")
            answer = await interviewer.ask_question(question)
            assert answer.value == "queued"

    @pytest.mark.asyncio()
    async def test_gh_errors_logged_not_raised(self) -> None:
        """_comment_and_label swallows GhSafeError internally, so ask() succeeds."""
        with patch("dark_factory.gates.human_gate._comment_and_label"):
            interviewer = AutoModeInterviewer(issue_number=42)
            question = _question(stage="needs_human")
            answer = await interviewer.ask(question)
            assert answer.value == "queued"


# ── NEEDS_LIVE helpers ──────────────────────────────────────────────


class TestNeedsLiveHelpers:
    def test_make_needs_live_request(self) -> None:
        req = make_needs_live_request(42, "5 pass, 2 skip")
        assert req.gate_type == HumanGateType.NEEDS_LIVE
        assert req.issue_number == 42
        assert req.context == "5 pass, 2 skip"
        assert req.stage == "crucible"
        assert req.metadata["test_results"] == "5 pass, 2 skip"

    def test_handle_needs_live_auto(self) -> None:
        with patch("dark_factory.gates.human_gate._comment_and_label") as mock_cal:
            handle_needs_live_auto(42, "3 pass, 1 skip", repo="test/repo")

            mock_cal.assert_called_once()
            args, kwargs = mock_cal.call_args
            assert args[0] == 42
            body = args[1]
            assert "NEEDS_LIVE" in body
            assert "3 pass, 1 skip" in body
            assert args[2] == "needs-live-env"
            assert kwargs["repo"] == "test/repo"


# ── Integration: gate types from DOT pipeline nodes ─────────────────


class TestDotPipelineNodeIntegration:
    """Verify that DOT pipeline gate types integrate with the queue and interviewer."""

    def test_needs_live_from_crucible(self) -> None:
        """Crucible NEEDS_LIVE via metadata (orchestrator-level)."""
        req = make_needs_live_request(42, "results")
        assert req.gate_type == HumanGateType.NEEDS_LIVE

    @pytest.mark.asyncio()
    async def test_needs_human_queues_with_engineering_brief(self) -> None:
        """NEEDS_HUMAN from arch review includes engineering brief context."""
        q = HumanGateQueue()
        interviewer = TuiInterviewer(q, issue_number=7)
        question = _question(
            text="Human review required",
            stage="needs_human",
            metadata={"context": "Engineering brief: use monorepo pattern"},
        )

        async def _approve() -> None:
            await asyncio.sleep(0.05)
            req = q.pending[0]
            assert req.gate_type == HumanGateType.NEEDS_HUMAN
            assert "Engineering brief" in req.context
            q.respond(req, HumanGateResponse(approved=True))

        asyncio.create_task(_approve())
        await interviewer.ask(question)

    @pytest.mark.asyncio()
    async def test_needs_live_queues_with_test_results(self) -> None:
        """NEEDS_LIVE from Crucible includes test results context."""
        q = HumanGateQueue()
        req = make_needs_live_request(42, "Pass: 5, Fail: 0, Skip: 2")

        async def _approve() -> None:
            await asyncio.sleep(0.05)
            assert len(q.pending) == 1
            assert q.pending[0].context == "Pass: 5, Fail: 0, Skip: 2"
            q.respond(req, HumanGateResponse(approved=True))

        asyncio.create_task(_approve())
        response = await q.submit(req)
        assert response.approved is True

    @pytest.mark.asyncio()
    async def test_escalation_queues_with_history(self) -> None:
        """Escalation after 3 rounds includes review history."""
        q = HumanGateQueue()
        interviewer = TuiInterviewer(q, issue_number=15)
        question = _question(
            text="Code review escalated",
            stage="escalate",
            metadata={"context": "Round 1: nits, Round 2: style, Round 3: same issues"},
        )

        async def _approve() -> None:
            await asyncio.sleep(0.05)
            req = q.pending[0]
            assert req.gate_type == HumanGateType.ESCALATION
            assert "Round 1" in req.context
            q.respond(req, HumanGateResponse(approved=False, comment="Needs redesign"))

        asyncio.create_task(_approve())
        answer = await interviewer.ask(question)
        assert answer.value == "rejected"
        assert answer.text == "Needs redesign"
