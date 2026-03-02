"""Route-to-engineering -- thin facilitator delegating to DOT pipelines.

Acquires a workspace (Python), then delegates each pipeline stage to the
:class:`~factory.pipeline.engine.FactoryPipelineEngine`:

1. Sentinel Gate 1 (post-clone security scan) -- BLOCK exits early.
2. Dark Forge (TDD build pipeline) -- handles specs, code gen, and tests.
3. Crucible (validation) -- three-way verdict: GO / NO_GO / NEEDS_LIVE.
4. Deploy pipeline -- on GO verdict only.
5. Label issue and notify (Python).

NO_GO feeds failure context back to Dark Forge for retry.
NEEDS_LIVE queues for human validation (comment + tag on ticket).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.engine.runner import PipelineResult
    from factory.pipeline.engine import FactoryPipelineEngine
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)

# ── Crucible verdict detection ──────────────────────────────────────
# The crucible.dot pipeline terminates at one of three Msquare exit
# nodes.  We detect the verdict from the last completed node ID.

_VERDICT_GO = "go"
_VERDICT_NO_GO = "no_go"
_VERDICT_NEEDS_LIVE = "needs_live"

# ── Labels ──────────────────────────────────────────────────────────

LABEL_NEEDS_LIVE = "factory:needs-live"


# ── Value types ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PipelineMetrics:
    """Timing for each pipeline stage."""

    workspace_seconds: float = 0.0
    sentinel_seconds: float = 0.0
    forge_seconds: float = 0.0
    crucible_seconds: float = 0.0
    deploy_seconds: float = 0.0
    total_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class RouteResult:
    """Outcome of the full engineering route."""

    success: bool
    verdict: str = ""
    error_message: str = ""
    pipeline_metrics: PipelineMetrics = field(default_factory=PipelineMetrics)


@dataclass(frozen=True, slots=True)
class RouteConfig:
    """Settings for the engineering pipeline."""

    repo: str = ""
    max_forge_retries: int = 1

    # Dependency injection hooks (for testing)
    engine_factory: Callable[[], FactoryPipelineEngine] | None = None
    acquire_workspace_fn: Callable[[str, int], Workspace] | None = None
    git_rev_parse_fn: Callable[[str, str], str] | None = None


# ── Helpers ─────────────────────────────────────────────────────────


def _inum(issue: dict[str, object]) -> int:
    raw = issue.get("number", 0)
    return int(raw) if isinstance(raw, (int, float, str)) else 0


def _ititle(issue: dict[str, object]) -> str:
    return str(issue.get("title", ""))


def _label_blocked(num: int, repo: str, reason: str) -> None:
    """Label issue as blocked."""
    try:
        from factory.integrations.gh_safe import add_label  # noqa: PLC0415

        add_label(num, "blocked", repo=repo)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to label issue #%d as blocked", num)
    logger.warning("Issue #%d blocked: %s", num, reason)


def _notify_needs_live(num: int, repo: str) -> None:
    """Comment on issue and add NEEDS_LIVE label for human validation."""
    body = (
        "**Dark Factory -- NEEDS_LIVE verdict**\n\n"
        "Crucible produced a `NEEDS_LIVE` verdict for this issue.\n"
        "Some tests require live/external services not available in CI.\n\n"
        "Please validate manually in a live environment."
    )
    try:
        from factory.integrations.gh_safe import add_label, comment_on_issue  # noqa: PLC0415

        comment_on_issue(num, body, repo=repo)
        add_label(num, LABEL_NEEDS_LIVE, repo=repo)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to notify NEEDS_LIVE for #%d", num)


def _label_done(num: int, repo: str) -> None:
    """Label issue as done after successful deploy."""
    try:
        from factory.integrations.gh_safe import add_label  # noqa: PLC0415

        add_label(num, "factory:done", repo=repo)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to label issue #%d as done", num)


def _fail(msg: str, num: int, repo: str, m: PipelineMetrics) -> RouteResult:
    logger.error("Pipeline failed for #%d: %s", num, msg)
    _label_blocked(num, repo, msg)
    return RouteResult(success=False, error_message=msg, pipeline_metrics=m)


def _default_acquire(repo: str, num: int) -> Workspace:
    from factory.workspace.manager import acquire_workspace  # noqa: PLC0415

    return acquire_workspace(repo, num)


def _default_engine() -> FactoryPipelineEngine:
    from factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415

    return FactoryPipelineEngine()


def _default_rev_parse(ws_path: str, ref: str) -> str:
    from factory.integrations.shell import git  # noqa: PLC0415

    result = git(["rev-parse", ref], cwd=ws_path)
    return result.stdout.strip()


def _extract_verdict(result: PipelineResult) -> str:
    """Extract the crucible verdict from the pipeline result.

    Checks the last completed node against known verdict exit nodes.
    Falls back to the context ``verdict`` variable if set.
    """
    from factory.engine.runner import PipelineStatus  # noqa: PLC0415

    if result.status == PipelineStatus.FAILED:
        return _VERDICT_NO_GO

    if result.completed_nodes:
        last_node = result.completed_nodes[-1]
        if last_node in {_VERDICT_GO, _VERDICT_NO_GO, _VERDICT_NEEDS_LIVE}:
            return last_node

    # Fallback: check context for explicit verdict variable
    verdict = result.context.get("verdict", "")
    if verdict in {_VERDICT_GO, _VERDICT_NO_GO, _VERDICT_NEEDS_LIVE}:
        return verdict

    return _VERDICT_NO_GO


def _is_pipeline_ok(result: PipelineResult) -> bool:
    """Check if a pipeline result indicates success."""
    from factory.engine.runner import PipelineStatus  # noqa: PLC0415

    return result.status == PipelineStatus.COMPLETED


# ── Main orchestrator ───────────────────────────────────────────────


async def route_to_engineering(
    issue: dict[str, object],
    config: RouteConfig,
) -> RouteResult:
    """Orchestrate the full issue-to-deploy pipeline via DOT engine.

    Sequence:
        1. Acquire workspace (Python)
        2. Sentinel Gate 1 -- BLOCK exits early
        3. Dark Forge -- full TDD build pipeline
        4. Crucible -- validation with three-way verdict
        5. On GO: deploy pipeline
        6. Label issue, notify (Python)

    NO_GO feeds failure context back to forge for retry.
    NEEDS_LIVE comments on the ticket and adds a label.
    """
    t0 = time.monotonic()
    num, repo = _inum(issue), config.repo
    ws_s = sen_s = forge_s = cruc_s = dep_s = 0.0

    def _m() -> PipelineMetrics:
        return PipelineMetrics(
            workspace_seconds=ws_s,
            sentinel_seconds=sen_s,
            forge_seconds=forge_s,
            crucible_seconds=cruc_s,
            deploy_seconds=dep_s,
            total_seconds=round(time.monotonic() - t0, 2),
        )

    # ── Step 1: Acquire workspace (Python) ──────────────────────
    logger.info("Routing issue #%d to engineering pipeline", num)
    acquire_fn = config.acquire_workspace_fn or _default_acquire
    try:
        s = time.monotonic()
        workspace = acquire_fn(repo, num)
        ws_s = round(time.monotonic() - s, 2)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Workspace acquisition failed: {exc}", num, repo, _m())

    ws_path = workspace.path

    # ── Step 2: Sentinel Gate 1 -- BLOCK exits early ────────────
    engine_fn = config.engine_factory or _default_engine
    engine = engine_fn()

    try:
        s = time.monotonic()
        sentinel_result = await engine.run_sentinel_gate(1, ws_path)
        sen_s = round(time.monotonic() - s, 2)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Sentinel Gate 1 failed: {exc}", num, repo, _m())

    if not _is_pipeline_ok(sentinel_result):
        return _fail(
            f"Sentinel Gate 1 BLOCK: {sentinel_result.error or 'security scan failed'}",
            num, repo, _m(),
        )

    # ── Step 3: Dark Forge -- handles TDD internally ────────────
    issue_data: dict[str, Any] = dict(issue)
    forge_attempts = 0
    max_attempts = 1 + config.max_forge_retries
    failure_context = ""

    rev_parse = config.git_rev_parse_fn or _default_rev_parse

    for attempt in range(1, max_attempts + 1):
        forge_attempts = attempt
        logger.info("Dark Forge attempt %d/%d for #%d", attempt, max_attempts, num)

        # Record base SHA before forge runs
        try:
            base_sha = rev_parse(ws_path, "HEAD")
        except Exception:  # noqa: BLE001
            base_sha = ""

        forge_ctx: dict[str, Any] = {}
        if failure_context:
            forge_ctx["crucible_failure"] = failure_context

        try:
            s = time.monotonic()
            forge_result = await engine.run_forge(issue_data, ws_path, context=forge_ctx)
            forge_s += round(time.monotonic() - s, 2)
        except Exception as exc:  # noqa: BLE001
            failure_context = f"Forge attempt {attempt} exception: {exc}"
            logger.warning("Dark Forge exception (attempt %d/%d): %s", attempt, max_attempts, exc)
            continue

        if not _is_pipeline_ok(forge_result):
            failure_context = f"Forge attempt {attempt} failed: {forge_result.error or 'unknown'}"
            logger.warning("Dark Forge failed (attempt %d/%d) for #%d", attempt, max_attempts, num)
            continue

        # Get head SHA after forge
        try:
            head_sha = rev_parse(ws_path, "HEAD")
        except Exception:  # noqa: BLE001
            head_sha = ""

        # ── Step 4: Crucible -- three-way verdict ───────────────
        try:
            s = time.monotonic()
            crucible_result = await engine.run_crucible(ws_path, base_sha, head_sha)
            cruc_s += round(time.monotonic() - s, 2)
        except Exception as exc:  # noqa: BLE001
            failure_context = f"Crucible exception: {exc}"
            logger.warning("Crucible exception for #%d: %s", num, exc)
            continue

        verdict = _extract_verdict(crucible_result)

        if verdict == _VERDICT_GO:
            # ── Step 5: Deploy pipeline ─────────────────────────
            try:
                s = time.monotonic()
                deploy_result = await engine.run_pipeline(
                    "deploy",
                    {"workspace": ws_path, "issue": issue_data, "branch": workspace.branch},
                )
                dep_s = round(time.monotonic() - s, 2)
            except Exception as exc:  # noqa: BLE001
                return _fail(f"Deploy pipeline failed: {exc}", num, repo, _m())

            if not _is_pipeline_ok(deploy_result):
                return _fail(
                    f"Deploy pipeline failed: {deploy_result.error or 'unknown'}",
                    num, repo, _m(),
                )

            # ── Step 6: Label issue, notify ─────────────────────
            _label_done(num, repo)
            logger.info("Pipeline succeeded for #%d (GO)", num)
            return RouteResult(
                success=True,
                verdict=_VERDICT_GO,
                pipeline_metrics=_m(),
            )

        if verdict == _VERDICT_NEEDS_LIVE:
            _notify_needs_live(num, repo)
            logger.info("Pipeline NEEDS_LIVE for #%d -- queued for human validation", num)
            return RouteResult(
                success=False,
                verdict=_VERDICT_NEEDS_LIVE,
                error_message="Crucible NEEDS_LIVE: queued for human validation",
                pipeline_metrics=_m(),
            )

        # NO_GO -- feed failure context back to forge for retry
        error_detail = crucible_result.error or "tests failed"
        failure_context = f"Crucible NO_GO: {error_detail}"
        logger.warning("Crucible NO_GO for #%d -- retrying forge (%d/%d)", num, attempt, max_attempts)

    # All forge attempts exhausted
    return _fail(
        f"Dark Forge failed after {forge_attempts} attempt(s): {failure_context}",
        num, repo, _m(),
    )


def route_to_engineering_sync(
    issue: dict[str, object],
    config: RouteConfig,
) -> RouteResult:
    """Synchronous wrapper around :func:`route_to_engineering`.

    Convenience for callers that are not already in an async context.
    """
    return asyncio.run(route_to_engineering(issue, config))
