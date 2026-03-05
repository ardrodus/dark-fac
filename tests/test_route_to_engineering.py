"""Tests for factory.pipeline.route_to_engineering — engine-based orchestrator."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

from dark_factory.engine.runner import PipelineResult, PipelineStatus
from dark_factory.pipeline.route_to_engineering import (
    RouteConfig,
    _extract_verdict,
    _is_pipeline_ok,
    route_to_engineering,
    route_to_engineering_sync,
)
from dark_factory.workspace.manager import Workspace

# ── Helpers ──────────────────────────────────────────────────────────


def _issue(
    number: int = 42,
    title: str = "Test issue",
) -> dict[str, object]:
    return {"number": number, "title": title, "body": "test body"}


def _workspace(
    path: str = "/tmp/ws",
    branch: str = "dark-factory/issue-42",
) -> Workspace:
    return Workspace(
        name="test/repo",
        path=path,
        repo_url="https://github.com/test/repo.git",
        branch=branch,
    )


def _pipeline_ok(
    context: dict[str, Any] | None = None,
    completed_nodes: list[str] | None = None,
) -> PipelineResult:
    return PipelineResult(
        status=PipelineStatus.COMPLETED,
        context=context or {},
        completed_nodes=completed_nodes or [],
    )


def _pipeline_fail(error: str = "failed") -> PipelineResult:
    return PipelineResult(
        status=PipelineStatus.FAILED,
        error=error,
    )


class FakeEngine:
    """Fake FactoryPipelineEngine with configurable responses."""

    def __init__(
        self,
        *,
        sentinel_result: PipelineResult | None = None,
        forge_result: PipelineResult | None = None,
        crucible_result: PipelineResult | None = None,
        deploy_result: PipelineResult | None = None,
    ) -> None:
        self._sentinel = sentinel_result or _pipeline_ok()
        self._forge = forge_result or _pipeline_ok()
        self._crucible = crucible_result or _pipeline_ok(completed_nodes=["go"])
        self._deploy = deploy_result or _pipeline_ok()
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def run_forge(
        self, issue: dict[str, Any], workspace: str, **kwargs: Any,
    ) -> PipelineResult:
        self.calls.append(("run_forge", (issue, workspace), kwargs))
        return self._forge

    async def run_pipeline(
        self, name: str, context: dict[str, Any] | None = None, **kwargs: Any,
    ) -> PipelineResult:
        self.calls.append(("run_pipeline", (name, context), kwargs))
        if name == "sentinel":
            return self._sentinel
        if name == "crucible":
            return self._crucible
        return self._deploy


def _config(
    engine: FakeEngine | None = None,
    **overrides: Any,
) -> RouteConfig:
    """Build test config with all external deps stubbed."""
    eng = engine or FakeEngine()
    defaults: dict[str, Any] = {
        "repo": "test/repo",
        "engine_factory": lambda: eng,
        "acquire_workspace_fn": lambda repo, n: _workspace(),
        "git_rev_parse_fn": lambda ws, ref: "abc123" if ref == "HEAD" else "def456",
    }
    defaults.update(overrides)
    return RouteConfig(**defaults)


# ── _extract_verdict ─────────────────────────────────────────────────


class TestExtractVerdict:
    def test_go_from_completed_nodes(self) -> None:
        r = _pipeline_ok(completed_nodes=["start", "run_tests", "go"])
        assert _extract_verdict(r) == "go"

    def test_no_go_from_completed_nodes(self) -> None:
        r = _pipeline_ok(completed_nodes=["start", "run_tests", "no_go"])
        assert _extract_verdict(r) == "no_go"

    def test_needs_live_from_completed_nodes(self) -> None:
        r = _pipeline_ok(completed_nodes=["start", "run_tests", "needs_live"])
        assert _extract_verdict(r) == "needs_live"

    def test_failed_pipeline_returns_no_go(self) -> None:
        r = _pipeline_fail("some error")
        assert _extract_verdict(r) == "no_go"

    def test_fallback_to_context_verdict(self) -> None:
        r = _pipeline_ok(
            context={"verdict": "needs_live"},
            completed_nodes=["start", "unknown_node"],
        )
        assert _extract_verdict(r) == "needs_live"

    def test_unknown_last_node_no_context(self) -> None:
        r = _pipeline_ok(completed_nodes=["start", "unknown_node"])
        assert _extract_verdict(r) == "no_go"

    def test_empty_completed_nodes(self) -> None:
        r = _pipeline_ok(completed_nodes=[])
        assert _extract_verdict(r) == "no_go"


# ── _is_pipeline_ok ──────────────────────────────────────────────────


class TestIsPipelineOk:
    def test_completed_is_ok(self) -> None:
        assert _is_pipeline_ok(_pipeline_ok()) is True

    def test_failed_is_not_ok(self) -> None:
        assert _is_pipeline_ok(_pipeline_fail()) is False

    def test_cancelled_is_not_ok(self) -> None:
        r = PipelineResult(status=PipelineStatus.CANCELLED)
        assert _is_pipeline_ok(r) is False


# ── Full pipeline: GO path ───────────────────────────────────────────


class TestRouteToEngineeringGo:
    def test_full_success_path(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["go"]),
        )
        cfg = _config(engine=engine)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is True
        assert result.verdict == "go"
        assert result.error_message == ""
        assert result.pipeline_metrics.workspace_seconds >= 0
        assert result.pipeline_metrics.total_seconds >= 0

    def test_calls_engine_in_order(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["go"]),
        )
        cfg = _config(engine=engine)
        asyncio.run(route_to_engineering(_issue(), cfg))

        call_names = [c[0] for c in engine.calls]
        assert call_names == [
            "run_pipeline",  # sentinel
            "run_forge",
            "run_pipeline",  # crucible
            "run_pipeline",  # deploy
        ]

    def test_sentinel_called_via_run_pipeline(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["go"]),
        )
        cfg = _config(engine=engine)
        asyncio.run(route_to_engineering(_issue(), cfg))

        sentinel_call = engine.calls[0]
        assert sentinel_call[0] == "run_pipeline"
        assert sentinel_call[1][0] == "sentinel"
        assert sentinel_call[1][1]["workspace_root"] == "/tmp/ws"

    def test_deploy_called_with_context(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["go"]),
        )
        cfg = _config(engine=engine)
        asyncio.run(route_to_engineering(_issue(), cfg))

        deploy_call = engine.calls[-1]
        assert deploy_call[0] == "run_pipeline"
        assert deploy_call[1][0] == "deploy"
        ctx = deploy_call[1][1]
        assert ctx["workspace"] == "/tmp/ws"
        assert ctx["branch"] == "dark-factory/issue-42"

    @patch("dark_factory.pipeline.route_to_engineering._label_done")
    def test_labels_issue_done_on_go(self, mock_label: MagicMock) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["go"]),
        )
        cfg = _config(engine=engine)
        asyncio.run(route_to_engineering(_issue(number=99), cfg))

        mock_label.assert_called_once_with(99, "test/repo")


# ── Sentinel BLOCK ───────────────────────────────────────────────────


class TestSentinelBlock:
    def test_sentinel_contaminated_exits_early(self) -> None:
        engine = FakeEngine(
            sentinel_result=_pipeline_fail("security scan blocked"),
        )
        cfg = _config(engine=engine)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is False
        assert "Sentinel CONTAMINATED" in result.error_message
        # No forge/crucible/deploy calls — only sentinel pipeline
        call_names = [c[0] for c in engine.calls]
        assert call_names == ["run_pipeline"]

    def test_sentinel_exception_exits_early(self) -> None:
        class ExplodingEngine(FakeEngine):
            async def run_pipeline(self, name: str, context: dict[str, Any] | None = None, **kw: Any) -> PipelineResult:
                if name == "sentinel":
                    raise RuntimeError("connection lost")
                return await super().run_pipeline(name, context, **kw)

        engine = ExplodingEngine()
        cfg = _config(engine=engine)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is False
        assert "Sentinel scan failed" in result.error_message


# ── Forge failure ────────────────────────────────────────────────────


class TestForgeFailure:
    def test_forge_fail_exhausts_retries(self) -> None:
        engine = FakeEngine(
            forge_result=_pipeline_fail("build error"),
        )
        cfg = _config(engine=engine, max_forge_retries=1)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is False
        assert "Dark Forge failed after 2 attempt" in result.error_message

    def test_forge_retries_correct_count(self) -> None:
        engine = FakeEngine(
            forge_result=_pipeline_fail("build error"),
        )
        cfg = _config(engine=engine, max_forge_retries=2)
        asyncio.run(route_to_engineering(_issue(), cfg))

        forge_calls = [c for c in engine.calls if c[0] == "run_forge"]
        assert len(forge_calls) == 3  # 1 + 2 retries


# ── Crucible NO_GO → forge retry ─────────────────────────────────────


class TestCrucibleNoGo:
    def test_no_go_retries_forge(self) -> None:
        call_count = {"forge": 0}

        class RetryEngine(FakeEngine):
            async def run_forge(self, issue: dict[str, Any], workspace: str, **kw: Any) -> PipelineResult:
                self.calls.append(("run_forge", (issue, workspace), kw))
                call_count["forge"] += 1
                return _pipeline_ok()

            async def run_pipeline(self, name: str, context: dict[str, Any] | None = None, **kw: Any) -> PipelineResult:
                self.calls.append(("run_pipeline", (name, context), kw))
                if name == "crucible":
                    # Always NO_GO
                    return _pipeline_ok(completed_nodes=["no_go"])
                return self._deploy

        engine = RetryEngine()
        cfg = _config(engine=engine, max_forge_retries=1)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is False
        assert call_count["forge"] == 2  # initial + 1 retry
        assert "NO_GO" in result.error_message or "Dark Forge failed" in result.error_message

    def test_no_go_then_go_on_retry(self) -> None:
        call_count = {"crucible": 0}

        class RetryThenGoEngine(FakeEngine):
            async def run_forge(self, issue: dict[str, Any], workspace: str, **kw: Any) -> PipelineResult:
                self.calls.append(("run_forge", (issue, workspace), kw))
                return _pipeline_ok()

            async def run_pipeline(self, name: str, context: dict[str, Any] | None = None, **kw: Any) -> PipelineResult:
                self.calls.append(("run_pipeline", (name, context), kw))
                if name == "crucible":
                    call_count["crucible"] += 1
                    if call_count["crucible"] == 1:
                        return _pipeline_ok(completed_nodes=["no_go"])
                    return _pipeline_ok(completed_nodes=["go"])
                return self._deploy

        engine = RetryThenGoEngine()
        cfg = _config(engine=engine, max_forge_retries=1)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is True
        assert result.verdict == "go"

    def test_no_go_passes_failure_context_to_forge(self) -> None:
        call_count = {"crucible": 0}

        class ContextEngine(FakeEngine):
            async def run_forge(self, issue: dict[str, Any], workspace: str, **kw: Any) -> PipelineResult:
                self.calls.append(("run_forge", (issue, workspace), kw))
                return _pipeline_ok()

            async def run_pipeline(self, name: str, context: dict[str, Any] | None = None, **kw: Any) -> PipelineResult:
                self.calls.append(("run_pipeline", (name, context), kw))
                if name == "crucible":
                    call_count["crucible"] += 1
                    if call_count["crucible"] == 1:
                        return PipelineResult(
                            status=PipelineStatus.COMPLETED,
                            completed_nodes=["no_go"],
                            error="real bug found",
                        )
                    return _pipeline_ok(completed_nodes=["go"])
                return self._deploy

        engine = ContextEngine()
        cfg = _config(engine=engine, max_forge_retries=1)
        asyncio.run(route_to_engineering(_issue(), cfg))

        # Second forge call should have crucible failure context
        forge_calls = [c for c in engine.calls if c[0] == "run_forge"]
        assert len(forge_calls) == 2
        second_forge_ctx = forge_calls[1][2].get("context", {})
        assert "crucible_failure" in second_forge_ctx


# ── NEEDS_LIVE ───────────────────────────────────────────────────────


class TestCrucibleNeedsLive:
    def test_needs_live_returns_immediately(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["needs_live"]),
        )
        cfg = _config(engine=engine)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is False
        assert result.verdict == "needs_live"
        assert "NEEDS_LIVE" in result.error_message

    @patch("dark_factory.pipeline.route_to_engineering._notify_needs_live")
    def test_needs_live_notifies(self, mock_notify: MagicMock) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["needs_live"]),
        )
        cfg = _config(engine=engine)
        asyncio.run(route_to_engineering(_issue(number=55), cfg))

        mock_notify.assert_called_once_with(55, "test/repo")

    def test_needs_live_no_deploy(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["needs_live"]),
        )
        cfg = _config(engine=engine)
        asyncio.run(route_to_engineering(_issue(), cfg))

        # Crucible calls run_pipeline("crucible"), but deploy should NOT be called
        deploy_calls = [c for c in engine.calls if c[0] == "run_pipeline" and c[1][0] == "deploy"]
        assert deploy_calls == []


# ── Workspace acquisition failure ────────────────────────────────────


class TestWorkspaceFailure:
    def test_acquire_exception_fails_pipeline(self) -> None:
        def failing_acquire(repo: str, num: int) -> Workspace:
            raise RuntimeError("disk full")

        cfg = _config(acquire_workspace_fn=failing_acquire)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is False
        assert "Workspace acquisition failed" in result.error_message


# ── Deploy failure ───────────────────────────────────────────────────


class TestDeployFailure:
    def test_deploy_fail_returns_error(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["go"]),
            deploy_result=_pipeline_fail("push rejected"),
        )
        cfg = _config(engine=engine)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        assert result.success is False
        assert "Deploy pipeline failed" in result.error_message


# ── Sync wrapper ─────────────────────────────────────────────────────


class TestSyncWrapper:
    def test_sync_wrapper_works(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["go"]),
        )
        cfg = _config(engine=engine)
        result = route_to_engineering_sync(_issue(), cfg)

        assert result.success is True
        assert result.verdict == "go"


# ── Pipeline metrics ─────────────────────────────────────────────────


class TestPipelineMetrics:
    def test_metrics_populated_on_success(self) -> None:
        engine = FakeEngine(
            crucible_result=_pipeline_ok(completed_nodes=["go"]),
        )
        cfg = _config(engine=engine)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        m = result.pipeline_metrics
        assert m.workspace_seconds >= 0
        assert m.sentinel_seconds >= 0
        assert m.forge_seconds >= 0
        assert m.crucible_seconds >= 0
        assert m.deploy_seconds >= 0
        assert m.total_seconds >= 0

    def test_metrics_populated_on_failure(self) -> None:
        engine = FakeEngine(
            sentinel_result=_pipeline_fail("blocked"),
        )
        cfg = _config(engine=engine)
        result = asyncio.run(route_to_engineering(_issue(), cfg))

        m = result.pipeline_metrics
        assert m.total_seconds >= 0
        assert m.workspace_seconds >= 0
        assert m.sentinel_seconds >= 0


# ── Issue helpers ────────────────────────────────────────────────────


class TestIssueHelpers:
    def test_inum_and_ititle(self) -> None:
        """_inum extracts/converts issue number; _ititle extracts title."""
        from dark_factory.pipeline.route_to_engineering import _inum, _ititle

        assert _inum({"number": 42}) == 42
        assert _inum({"number": "7"}) == 7
        assert _inum({}) == 0
        assert _ititle({"title": "Fix bug"}) == "Fix bug"
        assert _ititle({}) == ""
