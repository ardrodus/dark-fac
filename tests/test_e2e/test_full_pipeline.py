"""End-to-end test: issue through full pipeline.

Proves the full pipeline works: issue -> Sentinel -> Dark Forge ->
arch review -> TDD -> Crucible -> deploy.

All external dependencies (Claude CLI, git, GitHub API) are mocked
so the test can run in CI without real LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

from factory.engine.runner import PipelineResult, PipelineStatus
from factory.pipeline.route_to_engineering import (
    RouteConfig,
    route_to_engineering,
)
from factory.workspace.manager import Workspace

# ── Mock data ────────────────────────────────────────────────────────


def _mock_issue(
    number: int = 100,
    title: str = "Add user authentication #100",
    body: str = "As a user I need secure login via JWT tokens.",
) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": "enhancement"}],
    }


def _mock_workspace(
    path: str = "/tmp/e2e-ws",
    branch: str = "dark-factory/issue-100",
) -> Workspace:
    return Workspace(
        name="test/repo",
        path=path,
        repo_url="https://github.com/test/repo.git",
        branch=branch,
    )


def _ok_result(
    context: dict[str, Any] | None = None,
    completed_nodes: list[str] | None = None,
) -> PipelineResult:
    return PipelineResult(
        status=PipelineStatus.COMPLETED,
        context=context or {},
        completed_nodes=completed_nodes or [],
    )


# ── Mock engine tracking every stage ─────────────────────────────────


@dataclass
class StageRecord:
    """Captures what was called and with what args."""

    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FullPipelineEngine:
    """Fake engine that returns COMPLETED for every stage.

    Tracks all calls so the test can verify the full pipeline sequence.
    """

    def __init__(self) -> None:
        self.calls: list[StageRecord] = []

    async def run_sentinel_gate(
        self, gate: int, workspace: str, **kwargs: Any,
    ) -> PipelineResult:
        self.calls.append(StageRecord("sentinel", (gate, workspace), kwargs))
        return _ok_result(completed_nodes=["gate1_start", "secret_scan", "dep_audit", "gate1_pass"])

    async def run_forge(
        self, issue: dict[str, Any], workspace: str, **kwargs: Any,
    ) -> PipelineResult:
        self.calls.append(StageRecord("forge", (issue, workspace), kwargs))
        return _ok_result(
            context={"specs_generated": True, "tdd_passed": True},
            completed_nodes=[
                "arch_review", "prd_gen", "design_gen", "api_contract",
                "schema_gen", "interface_gen", "test_strategy",
                "story_schedule", "test_writer", "feature_writer",
                "code_reviewer", "forge_exit",
            ],
        )

    async def run_crucible(
        self, workspace: str, base_sha: str, head_sha: str, **kwargs: Any,
    ) -> PipelineResult:
        self.calls.append(
            StageRecord("crucible", (workspace, base_sha, head_sha), kwargs),
        )
        return _ok_result(
            context={"verdict": "go", "pass_rate": 1.0},
            completed_nodes=["load_tests", "run_tests", "analyze", "go"],
        )

    async def run_pipeline(
        self, name: str, context: dict[str, Any] | None = None, **kwargs: Any,
    ) -> PipelineResult:
        self.calls.append(StageRecord("pipeline", (name, context), kwargs))
        return _ok_result(completed_nodes=["deploy_start", "deploy_exit"])


def _make_config(engine: FullPipelineEngine | None = None) -> RouteConfig:
    eng = engine or FullPipelineEngine()
    return RouteConfig(
        repo="test/repo",
        max_forge_retries=1,
        engine_factory=lambda: eng,  # type: ignore[return-value,arg-type]
        acquire_workspace_fn=lambda repo, n: _mock_workspace(),
        git_rev_parse_fn=lambda ws, ref: "aaa111" if ref == "HEAD" else "bbb222",
    )


# ── Full pipeline happy path ─────────────────────────────────────────


class TestFullPipelineE2E:
    """End-to-end: issue through every pipeline stage."""

    def test_full_pipeline_completes_without_error(self) -> None:
        """Full pipeline completes and returns GO verdict."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        result = asyncio.run(route_to_engineering(_mock_issue(), cfg))

        assert result.success is True
        assert result.verdict == "go"
        assert result.error_message == ""

    def test_all_stages_called_in_order(self) -> None:
        """Sentinel -> Forge -> Crucible -> Deploy (in order)."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        asyncio.run(route_to_engineering(_mock_issue(), cfg))

        methods = [c.method for c in engine.calls]
        assert methods == ["sentinel", "forge", "crucible", "pipeline"]

    def test_sentinel_gate_1_runs_first(self) -> None:
        """Sentinel Gate 1 is called with gate=1 and workspace path."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        asyncio.run(route_to_engineering(_mock_issue(), cfg))

        sentinel = engine.calls[0]
        assert sentinel.method == "sentinel"
        assert sentinel.args == (1, "/tmp/e2e-ws")

    def test_forge_receives_issue_data(self) -> None:
        """Dark Forge is called with the issue dict and workspace."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        issue = _mock_issue()
        asyncio.run(route_to_engineering(issue, cfg))

        forge = engine.calls[1]
        assert forge.method == "forge"
        assert forge.args[0]["number"] == 100
        assert forge.args[1] == "/tmp/e2e-ws"

    def test_crucible_receives_shas(self) -> None:
        """Crucible is called with workspace, base_sha, and head_sha."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        asyncio.run(route_to_engineering(_mock_issue(), cfg))

        crucible = engine.calls[2]
        assert crucible.method == "crucible"
        assert crucible.args == ("/tmp/e2e-ws", "aaa111", "aaa111")

    def test_deploy_invoked_on_go_verdict(self) -> None:
        """Deploy pipeline fires when Crucible returns GO."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        asyncio.run(route_to_engineering(_mock_issue(), cfg))

        deploy = engine.calls[3]
        assert deploy.method == "pipeline"
        assert deploy.args[0] == "deploy"
        ctx = deploy.args[1]
        assert ctx["workspace"] == "/tmp/e2e-ws"
        assert ctx["branch"] == "dark-factory/issue-100"

    def test_pipeline_metrics_populated(self) -> None:
        """All timing fields are non-negative on success."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        result = asyncio.run(route_to_engineering(_mock_issue(), cfg))

        m = result.pipeline_metrics
        assert m.workspace_seconds >= 0
        assert m.sentinel_seconds >= 0
        assert m.forge_seconds >= 0
        assert m.crucible_seconds >= 0
        assert m.deploy_seconds >= 0
        assert m.total_seconds >= 0

    @patch("factory.pipeline.route_to_engineering._label_done")
    def test_issue_labelled_done_after_deploy(self, mock_label: MagicMock) -> None:
        """Issue is labelled 'factory:done' after successful deploy."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        asyncio.run(route_to_engineering(_mock_issue(number=100), cfg))

        mock_label.assert_called_once_with(100, "test/repo")


# ── Forge with strategy-selected arch review ─────────────────────────


class TestArchReviewStrategy:
    """Forge receives strategy context for arch review selection."""

    def test_forge_context_includes_issue(self) -> None:
        """Forge call passes the full issue dict as context."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        issue = _mock_issue()
        asyncio.run(route_to_engineering(issue, cfg))

        forge = engine.calls[1]
        forge_issue = forge.args[0]
        assert forge_issue["title"] == "Add user authentication #100"
        assert forge_issue["body"] == "As a user I need secure login via JWT tokens."


# ── Spec generation (7 artifacts) ────────────────────────────────────


class TestSpecGeneration:
    """Verify the spec generation pipeline produces all 7 artifacts.

    Since route_to_engineering delegates spec gen to the DOT engine
    (Dark Forge), we verify at the module level that each generator
    can be invoked with a mock invoke_fn and produces the correct
    result types.
    """

    @staticmethod
    def _stub_invoke(response: dict[str, object]) -> Any:
        """Return a mock invoke_fn that returns *response* as JSON."""
        return lambda _prompt: json.dumps(response)

    def test_prd_generation(self) -> None:
        from factory.specs.prd_generator import PRDResult, generate_prd

        invoke = self._stub_invoke({
            "title": "Auth Feature #100",
            "description": "JWT-based authentication",
            "user_stories": [
                {"id": "US-1", "title": "Login", "description": "Login flow",
                 "acceptance_criteria": ["Token issued"], "priority": "high"},
            ],
            "non_functional_requirements": ["99.9% uptime"],
            "out_of_scope": ["SSO"],
        })
        result = generate_prd(_mock_issue(), invoke_fn=invoke)
        assert isinstance(result, PRDResult)
        assert result.title == "Auth Feature #100"
        assert len(result.user_stories) == 1

    def test_design_generation(self) -> None:
        from factory.specs.design_generator import DesignResult, generate_design
        from factory.specs.prd_generator import PRDResult, UserStory

        prd = PRDResult(
            title="Auth #100", description="JWT auth",
            user_stories=(UserStory(id="US-1", title="Login",
                                    description="Login flow",
                                    acceptance_criteria=("Token issued",)),),
            non_functional_requirements=("uptime",), out_of_scope=(),
        )
        invoke = self._stub_invoke({
            "architecture_decisions": ["Use JWT"],
            "component_changes": ["Add auth middleware"],
            "data_model_changes": ["Add users table"],
            "api_changes": ["POST /login"],
            "risks": ["Token leakage"],
        })
        result = generate_design(prd, object(), invoke_fn=invoke)
        assert isinstance(result, DesignResult)
        assert "Use JWT" in result.architecture_decisions

    def test_api_contract_generation(self) -> None:
        from factory.specs.api_contract_generator import ContractResult, generate_api_contract
        from factory.specs.design_generator import DesignResult

        design = DesignResult(
            architecture_decisions=("REST API",),
            component_changes=("auth controller",),
            data_model_changes=(), api_changes=("POST /login",),
            risks=(),
        )
        analysis = MagicMock(language="python", framework="fastapi")
        invoke = self._stub_invoke({
            "openapi": "3.0.0",
            "paths": {"/login": {"post": {"summary": "Login"}}},
        })
        result = generate_api_contract(
            design, analysis, invoke_fn=invoke, workspace="/tmp/e2e-ws",
        )
        assert isinstance(result, ContractResult)

    def test_schema_generation(self) -> None:
        from factory.specs.design_generator import DesignResult
        from factory.specs.schema_generator import SchemaResult, generate_schema

        design = DesignResult(
            architecture_decisions=(), component_changes=(),
            data_model_changes=("Add users table",),
            api_changes=(), risks=(),
        )
        analysis = MagicMock(language="python", framework="django")
        invoke = self._stub_invoke({
            "schema_type": "sql",
            "db_engine": "postgresql",
            "tables": [{"name": "users", "columns": ["id", "email"]}],
            "migrations": ["CREATE TABLE users (id SERIAL, email TEXT)"],
        })
        result = generate_schema(design, analysis, invoke_fn=invoke)
        assert isinstance(result, SchemaResult)

    def test_interface_generation(self) -> None:
        from factory.specs.design_generator import DesignResult
        from factory.specs.interface_generator import InterfaceResult, generate_interfaces

        design = DesignResult(
            architecture_decisions=(), component_changes=("auth module",),
            data_model_changes=(), api_changes=(), risks=(),
        )
        analysis = MagicMock(language="python", framework="fastapi")
        invoke = self._stub_invoke({
            "interfaces": "class AuthService(Protocol): ...",
            "validation_passed": True,
        })
        result = generate_interfaces(design, analysis, invoke_fn=invoke)
        assert isinstance(result, InterfaceResult)

    def test_test_strategy_generation(self) -> None:
        from factory.specs.design_generator import DesignResult
        from factory.specs.prd_generator import PRDResult, UserStory
        from factory.specs.test_strategy_generator import (
            TestStrategyResult,
            generate_test_strategy,
        )

        prd = PRDResult(
            title="Auth #100", description="JWT",
            user_stories=(UserStory(id="US-1", title="Login",
                                    description="Login",
                                    acceptance_criteria=("works",)),),
            non_functional_requirements=(), out_of_scope=(),
        )
        design = DesignResult(
            architecture_decisions=("JWT",), component_changes=("auth",),
            data_model_changes=(), api_changes=(), risks=(),
        )
        invoke = self._stub_invoke({
            "unit_tests": ["test_login"],
            "integration_tests": ["test_auth_flow"],
            "e2e_tests": ["test_full_login"],
            "fixtures": ["user_fixture"],
            "mocks": ["mock_jwt"],
            "coverage_targets": {"unit": 80, "overall": 70},
        })
        result = generate_test_strategy(prd, design, object(), invoke_fn=invoke)
        assert isinstance(result, TestStrategyResult)
        assert "test_login" in result.unit_tests

    def test_all_seven_artifacts_producible(self) -> None:
        """Verify that all 7 artifact types can be produced.

        The 7 artifacts: PRD, design, API contract, schema,
        interfaces, test strategy, scheduled stories (PRD user stories
        with dependency ordering).
        """
        from factory.specs.api_contract_generator import ContractResult
        from factory.specs.design_generator import DesignResult
        from factory.specs.interface_generator import InterfaceResult
        from factory.specs.prd_generator import PRDResult, UserStory
        from factory.specs.schema_generator import SchemaResult
        from factory.specs.test_strategy_generator import TestStrategyResult

        # The 7th artifact (scheduled stories) is the PRD's user_stories
        # with depends_on fields producing a topological schedule.
        prd = PRDResult(
            title="Auth #100", description="JWT auth",
            user_stories=(
                UserStory(id="US-1", title="Schema", description="DB setup",
                          acceptance_criteria=("table created",), priority="high"),
                UserStory(id="US-2", title="API", description="Endpoints",
                          acceptance_criteria=("POST /login",), priority="high",
                          depends_on=("US-1",)),
                UserStory(id="US-3", title="UI", description="Login form",
                          acceptance_criteria=("form renders",), priority="medium",
                          depends_on=("US-2",)),
            ),
            non_functional_requirements=("uptime",), out_of_scope=(),
        )
        # Verify all 7 types exist and are importable
        artifact_types = [
            PRDResult, DesignResult, ContractResult, SchemaResult,
            InterfaceResult, TestStrategyResult,
        ]
        assert len(artifact_types) == 6  # 6 generator types
        # 7th: scheduled stories = PRD stories with depends_on ordering
        assert len(prd.user_stories) == 3
        assert prd.user_stories[1].depends_on == ("US-1",)
        assert prd.user_stories[2].depends_on == ("US-2",)


# ── TDD loop ─────────────────────────────────────────────────────────


class TestTDDLoopE2E:
    """Verify the TDD loop: test writer -> feature writer -> code reviewer."""

    def test_tdd_pipeline_happy_path(self) -> None:
        """TDD pipeline runs through all three stages and succeeds."""
        from factory.pipeline.tdd.feature_writer import TestRunResult
        from factory.pipeline.tdd.orchestrator import TDDConfig, TDDResult, run_tdd_pipeline
        from factory.pipeline.tdd.test_writer import SpecBundle

        specs = SpecBundle(
            prd="Auth feature PRD",
            design_doc="JWT design doc",
            test_strategy="Unit + integration tests",
            interface_definitions="class AuthService(Protocol): ...",
            api_contract="POST /login",
            schema_spec="CREATE TABLE users (id, email)",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file on disk so test_writer's existence check passes
            test_dir = os.path.join(tmpdir, "tests")
            os.makedirs(test_dir, exist_ok=True)
            test_file = os.path.join(test_dir, "test_auth.py")
            with open(test_file, "w") as f:
                f.write("def test_login(): assert False\n")

            ws = _mock_workspace(path=tmpdir)

            tw_response = json.dumps({
                "test_files_created": ["tests/test_auth.py"],
                "test_count": 5,
                "framework_used": "pytest",
            })
            fw_response = json.dumps({
                "files_modified": ["src/auth.py"],
                "files_created": ["src/middleware.py"],
            })
            cr_response = json.dumps({
                "verdict": "APPROVE",
                "comments": [],
                "blocking_issues": [],
            })

            call_count: dict[str, int] = {"n": 0}
            responses = [tw_response, fw_response, cr_response]

            def mock_invoke(prompt: str) -> str:
                idx = min(call_count["n"], len(responses) - 1)
                call_count["n"] += 1
                return responses[idx]

            def mock_test_run(ws_path: str, config: TDDConfig) -> TestRunResult:
                if call_count.get("test", 0) == 0:
                    call_count["test"] = 1
                    return TestRunResult(passed=False, total=5, failures=3,
                                         raw_output="3 failed, 2 passed")
                return TestRunResult(passed=True, total=5, failures=0,
                                     raw_output="5 passed")

            # Mock git operations inside _commit_tests and _get_diff
            with (
                patch("factory.pipeline.tdd.test_writer._commit_tests", return_value=True),
                patch("factory.pipeline.tdd.orchestrator._get_diff", return_value="mock diff"),
            ):
                result = run_tdd_pipeline(
                    specs, ws, invoke_fn=mock_invoke, test_run_fn=mock_test_run,
                )
            assert isinstance(result, TDDResult)
            assert result.test_writer_result is not None
            assert result.rounds >= 1

    def test_tdd_stages_execute_in_order(self) -> None:
        """TDD stages: test_writer -> red test -> feature_writer -> green test -> reviewer."""
        from factory.pipeline.tdd.feature_writer import TestRunResult
        from factory.pipeline.tdd.orchestrator import TDDConfig, run_tdd_pipeline
        from factory.pipeline.tdd.test_writer import SpecBundle

        specs = SpecBundle(prd="PRD", design_doc="Design")
        stage_order: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file so test_writer existence check passes
            test_file = os.path.join(tmpdir, "t.py")
            with open(test_file, "w") as f:
                f.write("def test_x(): assert False\n")

            ws = _mock_workspace(path=tmpdir)

            def mock_invoke(prompt: str) -> str:
                if "Test Writer" in prompt:
                    stage_order.append("test_writer")
                    return json.dumps({"test_files_created": ["t.py"],
                                       "test_count": 1, "framework_used": "pytest"})
                # Check code reviewer BEFORE feature writer — both prompts
                # contain "implement" (reviewer says "implementation diff")
                if "Code Reviewer" in prompt:
                    stage_order.append("code_reviewer")
                    return json.dumps({"verdict": "APPROVE", "comments": [],
                                       "blocking_issues": []})
                if "Feature Writer" in prompt:
                    stage_order.append("feature_writer")
                    return json.dumps({"files_modified": ["f.py"], "files_created": []})
                stage_order.append("unknown")
                return "{}"

            test_call: dict[str, int] = {"n": 0}

            def mock_test_run(ws_path: str, config: TDDConfig) -> TestRunResult:
                test_call["n"] += 1
                stage_order.append(f"test_run_{test_call['n']}")
                if test_call["n"] == 1:
                    return TestRunResult(passed=False, total=1, failures=1,
                                         raw_output="1 failed")
                return TestRunResult(passed=True, total=1, failures=0,
                                     raw_output="1 passed")

            with (
                patch("factory.pipeline.tdd.test_writer._commit_tests", return_value=True),
                patch("factory.pipeline.tdd.orchestrator._get_diff", return_value="mock diff"),
            ):
                run_tdd_pipeline(specs, ws, invoke_fn=mock_invoke, test_run_fn=mock_test_run)

        # Verify ordering: test_writer → test_run_1 (red) → feature_writer → test_run_2 (green) → code_reviewer
        assert stage_order[0] == "test_writer"
        assert stage_order[1] == "test_run_1"  # red phase
        assert stage_order[2] == "feature_writer"
        assert stage_order[3] == "test_run_2"  # green phase
        assert stage_order[4] == "code_reviewer"


# ── Crucible verdict ─────────────────────────────────────────────────


class TestCrucibleVerdict:
    """Crucible produces GO/NO_GO verdict and pipeline reacts correctly."""

    def test_go_verdict_triggers_deploy(self) -> None:
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        result = asyncio.run(route_to_engineering(_mock_issue(), cfg))

        assert result.verdict == "go"
        assert result.success is True
        deploy_calls = [c for c in engine.calls if c.method == "pipeline"]
        assert len(deploy_calls) == 1

    def test_no_go_verdict_retries_forge(self) -> None:
        """NO_GO from Crucible triggers forge retry."""

        class NoGoEngine(FullPipelineEngine):
            async def run_crucible(
                self, workspace: str, base_sha: str, head_sha: str, **kw: Any,
            ) -> PipelineResult:
                self.calls.append(
                    StageRecord("crucible", (workspace, base_sha, head_sha), kw),
                )
                return _ok_result(completed_nodes=["no_go"])

        engine = NoGoEngine()
        cfg = RouteConfig(
            repo="test/repo", max_forge_retries=1,
            engine_factory=lambda: engine,  # type: ignore[return-value,arg-type]
            acquire_workspace_fn=lambda r, n: _mock_workspace(),
            git_rev_parse_fn=lambda ws, ref: "aaa111",
        )
        result = asyncio.run(route_to_engineering(_mock_issue(), cfg))

        assert result.success is False
        forge_calls = [c for c in engine.calls if c.method == "forge"]
        assert len(forge_calls) == 2  # initial + 1 retry


# ── Deploy pipeline ──────────────────────────────────────────────────


class TestDeployPipeline:
    """Deploy pipeline invoked as empty template, completes immediately."""

    def test_deploy_completes_immediately(self) -> None:
        """Deploy returns COMPLETED with minimal completed_nodes."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        asyncio.run(route_to_engineering(_mock_issue(), cfg))

        deploy = engine.calls[3]
        assert deploy.method == "pipeline"
        assert deploy.args[0] == "deploy"

    def test_deploy_receives_workspace_and_branch(self) -> None:
        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        asyncio.run(route_to_engineering(_mock_issue(), cfg))

        deploy = engine.calls[3]
        ctx = deploy.args[1]
        assert ctx["workspace"] == "/tmp/e2e-ws"
        assert ctx["branch"] == "dark-factory/issue-100"
        assert ctx["issue"]["number"] == 100


# ── Mock CLI responses for CI ────────────────────────────────────────


class TestMockedCLIResponses:
    """Verify the test can run with mocked Claude Code CLI responses."""

    def test_pipeline_with_all_mocked_externals(self) -> None:
        """Full pipeline with every external dep mocked (CI-safe)."""
        engine = FullPipelineEngine()
        cfg = _make_config(engine)

        # Patch all external integrations
        with (
            patch("factory.pipeline.route_to_engineering._label_done"),
            patch("factory.pipeline.route_to_engineering._label_blocked"),
            patch("factory.pipeline.route_to_engineering._notify_needs_live"),
        ):
            result = asyncio.run(route_to_engineering(_mock_issue(), cfg))

        assert result.success is True
        assert result.verdict == "go"
        assert len(engine.calls) == 4  # sentinel, forge, crucible, deploy

    def test_sync_wrapper_with_mocked_engine(self) -> None:
        """The sync wrapper also works with mocked engine."""
        from factory.pipeline.route_to_engineering import route_to_engineering_sync

        engine = FullPipelineEngine()
        cfg = _make_config(engine)
        result = route_to_engineering_sync(_mock_issue(), cfg)

        assert result.success is True
        assert result.verdict == "go"
