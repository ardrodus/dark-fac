"""Tests for the Obelisk investigation pipeline.

Verifies that:
- Pipeline runs and reaches a terminal node for a factory bug scenario
- Pipeline routes to escalation for user code scenario
- Pipeline routes to escalation for infrastructure scenario
- Investigator returns correct verdict based on completed nodes
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dark_factory.engine.runner import PipelineResult, PipelineStatus
from dark_factory.obelisk.investigator import investigate
from dark_factory.obelisk.models import Alert, Investigation

# ── Helpers ──────────────────────────────────────────────────────────


def _make_alert(
    *,
    error_type: str = "RuntimeError",
    source: str = "dark_forge",
    pipeline: str = "dark_forge",
    node: str = "code_writer",
    message: str = "IndexError in code_writer node",
    signature: str = "dark_forge::code_writer::IndexError",
) -> Alert:
    return Alert(
        error_type=error_type,
        source=source,
        pipeline=pipeline,
        node=node,
        message=message,
        signature=signature,
    )


def _make_result(
    *,
    completed_nodes: list[str],
    status: PipelineStatus = PipelineStatus.COMPLETED,
    context: dict[str, Any] | None = None,
    duration_seconds: float = 1.5,
) -> PipelineResult:
    return PipelineResult(
        status=status,
        completed_nodes=completed_nodes,
        context=context or {},
        duration_seconds=duration_seconds,
    )


def _write_outcome(workspace: str, investigation_id: str, url: str) -> None:
    """Write an outcome JSON file like pipeline nodes do."""
    outcome_dir = Path(workspace) / ".dark-factory" / "obelisk"
    outcome_dir.mkdir(parents=True, exist_ok=True)
    outcome_path = outcome_dir / f"outcome-{investigation_id}.json"
    outcome_path.write_text(json.dumps({"url": url}), encoding="utf-8")


# ── Factory bug scenario: pipeline reaches terminal "fixed" node ─────


class TestFactoryBugScenario:
    """Pipeline runs the fix path for a factory bug and reaches 'fixed'."""

    def test_pipeline_reaches_fixed_terminal_for_factory_bug(
        self, tmp_path: Path,
    ) -> None:
        """Full fix path: gather -> analyze -> classify(FACTORY_BUG) ->
        propose_fix -> validate_fix -> apply_fix -> create_pr -> fixed."""
        factory_ws = str(tmp_path / "factory")
        user_ws = str(tmp_path / "user")
        alert = _make_alert()

        result = _make_result(
            completed_nodes=[
                "start", "gather_context", "analyze_failure", "classify",
                "propose_fix", "validate_fix", "fix_verdict",
                "apply_fix", "create_pr", "fixed",
            ],
        )

        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, user_ws))

        assert isinstance(inv, Investigation)
        assert inv.verdict == "FIXED"
        assert inv.alert is alert
        assert inv.duration_s == 1.5

        # Engine was called with correct pipeline name and context
        call_args = mock_engine.run_pipeline.call_args
        assert call_args[0][0] == "obelisk"
        ctx = call_args[0][1]
        assert ctx["workspace"] == factory_ws
        assert ctx["user_workspace"] == user_ws
        assert "investigation_id" in ctx

    def test_fixed_verdict_with_outcome_url(self, tmp_path: Path) -> None:
        """Outcome URL is read from disk when the pipeline writes it."""
        factory_ws = str(tmp_path / "factory")
        user_ws = str(tmp_path / "user")
        alert = _make_alert()

        result = _make_result(completed_nodes=["fixed"])
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        # We need to write the outcome file with the correct investigation_id.
        # Patch uuid to control the ID.
        with (
            patch(
                "dark_factory.obelisk.investigator.FactoryPipelineEngine",
                return_value=mock_engine,
            ),
            patch(
                "dark_factory.obelisk.investigator.uuid.uuid4",
            ) as mock_uuid,
        ):
            mock_uuid.return_value.hex = "aabbccdd12345678"
            inv_id = "inv-aabbccdd"
            _write_outcome(factory_ws, inv_id, "https://github.com/org/repo/pull/42")

            inv = asyncio.run(investigate(alert, factory_ws, user_ws))

        assert inv.verdict == "FIXED"
        assert inv.outcome_url == "https://github.com/org/repo/pull/42"


# ── User code scenario: pipeline routes to escalation ────────────────


class TestUserCodeScenario:
    """Pipeline escalates when the failure is caused by user code."""

    def test_pipeline_routes_to_escalation_for_user_code(
        self, tmp_path: Path,
    ) -> None:
        """Classify(USER_CODE) -> create_issue -> escalated."""
        factory_ws = str(tmp_path / "factory")
        user_ws = str(tmp_path / "user")
        alert = _make_alert(
            error_type="SyntaxError",
            source="user_repo",
            message="SyntaxError in user module",
            signature="dark_forge::code_writer::SyntaxError",
        )

        result = _make_result(
            completed_nodes=[
                "start", "gather_context", "analyze_failure", "classify",
                "create_issue", "escalated",
            ],
        )

        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, user_ws))

        assert inv.verdict == "ESCALATED"
        assert "fixed" not in result.completed_nodes

    def test_user_code_escalation_reads_issue_url(self, tmp_path: Path) -> None:
        """Outcome URL contains the created issue URL."""
        factory_ws = str(tmp_path / "factory")
        user_ws = str(tmp_path / "user")
        alert = _make_alert()

        result = _make_result(
            completed_nodes=["start", "classify", "create_issue", "escalated"],
        )
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with (
            patch(
                "dark_factory.obelisk.investigator.FactoryPipelineEngine",
                return_value=mock_engine,
            ),
            patch(
                "dark_factory.obelisk.investigator.uuid.uuid4",
            ) as mock_uuid,
        ):
            mock_uuid.return_value.hex = "11223344aabbccdd"
            inv_id = "inv-11223344"
            _write_outcome(factory_ws, inv_id, "https://github.com/org/repo/issues/99")

            inv = asyncio.run(investigate(alert, factory_ws, user_ws))

        assert inv.verdict == "ESCALATED"
        assert inv.outcome_url == "https://github.com/org/repo/issues/99"


# ── Infrastructure scenario: pipeline routes to escalation ───────────


class TestInfrastructureScenario:
    """Pipeline escalates when the failure is infrastructure-related."""

    def test_pipeline_routes_to_escalation_for_infrastructure(
        self, tmp_path: Path,
    ) -> None:
        """Classify(INFRASTRUCTURE) -> create_issue -> escalated."""
        factory_ws = str(tmp_path / "factory")
        user_ws = str(tmp_path / "user")
        alert = _make_alert(
            error_type="ConnectionError",
            source="infrastructure",
            message="GitHub API rate limit exceeded",
            signature="dark_forge::create_pr::ConnectionError",
        )

        result = _make_result(
            completed_nodes=[
                "start", "gather_context", "analyze_failure", "classify",
                "create_issue", "escalated",
            ],
        )

        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, user_ws))

        assert inv.verdict == "ESCALATED"
        assert "fixed" not in result.completed_nodes

    def test_infrastructure_escalation_with_fix_attempt(
        self, tmp_path: Path,
    ) -> None:
        """Factory bug where fix validation fails -> escalated_with_fix."""
        factory_ws = str(tmp_path / "factory")
        user_ws = str(tmp_path / "user")
        alert = _make_alert()

        result = _make_result(
            completed_nodes=[
                "start", "gather_context", "analyze_failure", "classify",
                "propose_fix", "validate_fix", "fix_verdict",
                "create_issue_with_fix", "escalated_with_fix",
            ],
        )

        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, user_ws))

        # escalated_with_fix is NOT "fixed" — verdict should be ESCALATED
        assert inv.verdict == "ESCALATED"


# ── Verdict logic ────────────────────────────────────────────────────


class TestVerdictFromCompletedNodes:
    """Investigator returns correct verdict based on completed_nodes."""

    def test_fixed_when_fixed_node_present(self, tmp_path: Path) -> None:
        factory_ws = str(tmp_path / "factory")
        alert = _make_alert()

        result = _make_result(completed_nodes=["start", "fixed"])
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, ""))

        assert inv.verdict == "FIXED"

    def test_escalated_when_only_escalated_node(self, tmp_path: Path) -> None:
        factory_ws = str(tmp_path / "factory")
        alert = _make_alert()

        result = _make_result(completed_nodes=["start", "escalated"])
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, ""))

        assert inv.verdict == "ESCALATED"

    def test_escalated_when_escalated_with_fix_node(
        self, tmp_path: Path,
    ) -> None:
        factory_ws = str(tmp_path / "factory")
        alert = _make_alert()

        result = _make_result(
            completed_nodes=["start", "escalated_with_fix"],
        )
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, ""))

        assert inv.verdict == "ESCALATED"

    def test_escalated_when_no_terminal_nodes(self, tmp_path: Path) -> None:
        """If pipeline completes without reaching a known terminal, verdict is ESCALATED."""
        factory_ws = str(tmp_path / "factory")
        alert = _make_alert()

        result = _make_result(completed_nodes=["start", "gather_context"])
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, ""))

        assert inv.verdict == "ESCALATED"

    def test_outcome_url_empty_when_no_file(self, tmp_path: Path) -> None:
        """When no outcome file exists, outcome_url is empty string."""
        factory_ws = str(tmp_path / "factory")
        alert = _make_alert()

        result = _make_result(completed_nodes=["fixed"])
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, ""))

        assert inv.outcome_url == ""

    def test_investigation_id_format(self, tmp_path: Path) -> None:
        """Investigation ID follows inv-{hex8} format."""
        factory_ws = str(tmp_path / "factory")
        alert = _make_alert()

        result = _make_result(completed_nodes=["fixed"])
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            inv = asyncio.run(investigate(alert, factory_ws, ""))

        assert inv.id.startswith("inv-")
        hex_part = inv.id[4:]
        assert len(hex_part) == 8
        int(hex_part, 16)  # should not raise

    def test_alert_serialized_in_context(self, tmp_path: Path) -> None:
        """Alert is JSON-serialized in the engine context."""
        factory_ws = str(tmp_path / "factory")
        alert = _make_alert(error_type="KeyError", message="missing key")

        result = _make_result(completed_nodes=["fixed"])
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value=result)

        with patch(
            "dark_factory.obelisk.investigator.FactoryPipelineEngine",
            return_value=mock_engine,
        ):
            asyncio.run(investigate(alert, factory_ws, ""))

        ctx = mock_engine.run_pipeline.call_args[0][1]
        alert_data = json.loads(ctx["alert"])
        assert alert_data["error_type"] == "KeyError"
        assert alert_data["message"] == "missing key"
