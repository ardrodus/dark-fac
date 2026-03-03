"""Auto mode — headless continuous loop for autonomous issue processing.

Watches for GitHub issues (poll-based), then runs the full cycle per issue:

  1. **Dark Forge** — pipeline execution with Sentinel gates at lifecycle points.
  2. **Crucible** — end-to-end test validation with Sentinel gates.
  3. **Deploy** — push the working branch to the remote.
  4. **Ouroboros** — capture learnings and optionally trigger self-improvement.

Verdict handling:

- ``GO`` → deploy the change and move to the next issue.
- ``NO_GO`` → feed failure context back to Dark Forge for retry.
- ``NEEDS_LIVE`` → comment on the ticket, add a label, queue for human validation.

Graceful shutdown on ``SIGINT`` / ``SIGTERM``.
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from dark_factory.crucible.orchestrator import CrucibleResult, CrucibleVerdict
from dark_factory.dispatch.issue_dispatcher import (
    LABEL_DONE,
    LABEL_FAILED,
    LABEL_IN_PROGRESS,
    LABEL_QUEUED,
    DispatcherState,
    select_next_issue,
)
from dark_factory.integrations.gh_safe import (
    GhSafeError,
    IssueInfo,
    add_label,
    comment_on_issue,
    remove_label,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)

# ── Labels ────────────────────────────────────────────────────────────

LABEL_ARCH_REVIEW = "arch-review"
LABEL_ARCH_APPROVED = "arch-approved"
LABEL_IN_REVIEW = "in-review"
LABEL_NEEDS_LIVE = "needs-live-env"

# ── Defaults ──────────────────────────────────────────────────────────

_DEFAULT_POLL_INTERVAL = 30.0
_DEFAULT_MAX_FORGE_RETRIES = 2


# ── Value types ───────────────────────────────────────────────────────


class CycleOutcome(Enum):
    """Possible outcomes for a full auto-mode cycle."""

    SUCCESS = "success"
    NO_GO = "no_go"
    NEEDS_LIVE = "needs_live"
    FORGE_FAILED = "forge_failed"
    DEPLOY_FAILED = "deploy_failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CycleResult:
    """Outcome of processing a single issue through the full cycle."""

    issue_number: int
    outcome: CycleOutcome
    message: str
    forge_attempts: int = 0
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class AutoModeConfig:
    """Configuration for the auto-mode loop."""

    repo: str = ""
    cwd: str | None = None
    poll_interval: float = _DEFAULT_POLL_INTERVAL
    max_forge_retries: int = _DEFAULT_MAX_FORGE_RETRIES
    max_cycles: int | None = None

    # Dependency injection hooks (for testing and customisation)
    forge_fn: Callable[[IssueInfo, str], bool] | None = None
    crucible_fn: Callable[[Workspace, int], CrucibleResult] | None = None
    deploy_fn: Callable[[Workspace, int], bool] | None = None
    ouroboros_fn: Callable[[IssueInfo, CycleOutcome, str], None] | None = None
    acquire_workspace_fn: Callable[[str, int], Workspace] | None = None
    sentinel_fn: Callable[[str, str], bool] | None = None
    sleep_fn: Callable[[float], None] | None = None


@dataclass(slots=True)
class AutoModeState:
    """Mutable runtime state for the auto-mode loop."""

    shutdown_requested: bool = False
    cycles_completed: int = 0
    results: list[CycleResult] = field(default_factory=list)
    dispatcher: DispatcherState = field(default_factory=DispatcherState)


# ── Signal handling ───────────────────────────────────────────────────


def _install_signal_handlers(state: AutoModeState) -> None:
    """Register SIGINT/SIGTERM handlers that set the shutdown flag."""

    def _handler(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — requesting graceful shutdown", sig_name)
        state.shutdown_requested = True

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


# ── Dark Forge (pipeline) ────────────────────────────────────────────


def _run_sentinel_gate(workspace_path: str, phase: str, *, sentinel_fn: Callable[[str, str], bool] | None) -> bool:
    """Fire a Sentinel gate at a lifecycle point inside a phase.

    Returns ``True`` if the gate passes (or no sentinel function is configured).
    """
    if sentinel_fn is None:
        return _default_sentinel(workspace_path, phase)
    return sentinel_fn(workspace_path, phase)


def _default_sentinel(workspace_path: str, phase: str) -> bool:
    """Run default Sentinel gate checks using the real gate framework.

    Only runs security-relevant gates (secret-scan, dependency-scan) —
    not quality, startup-health, or other gates that assume the workspace
    is the factory itself.
    """
    from pathlib import Path  # noqa: PLC0415

    from dark_factory.gates.framework import (  # noqa: PLC0415
        GateReport,
        UnifiedReport,
        run_gate_by_name,
        write_gate_report,
    )

    _SECURITY_GATES = ("secret-scan", "dependency-scan")
    metrics_dir = Path(workspace_path) / ".dark-factory"
    logger.info("Running sentinel-%s gates on %s", phase, workspace_path)

    reports: list[GateReport] = []
    for gate_name in _SECURITY_GATES:
        try:
            report = run_gate_by_name(gate_name, workspace=workspace_path, metrics_dir=metrics_dir)
            reports.append(report)
            logger.info("  Gate %s: %s", gate_name, "PASSED" if report.passed else "FAILED")
        except KeyError:
            logger.debug("Gate %s not registered — skipping", gate_name)
        except Exception:  # noqa: BLE001
            logger.warning("Gate %s failed to run", gate_name, exc_info=True)

    if not reports:
        logger.info("Sentinel %s: no security gates ran (all skipped)", phase)
        return True

    unified = UnifiedReport(gate_reports=tuple(reports))
    write_gate_report(unified, report_dir=metrics_dir)
    logger.info(
        "Sentinel %s: %s (%d gate(s))",
        phase, "PASSED" if unified.overall_passed else "FAILED",
        len(reports),
    )
    return unified.overall_passed


def _default_forge(
    issue: IssueInfo,
    workspace_path: str,
    *,
    skip_arch_review: bool = False,
    on_event: Callable[[object], None] | None = None,
) -> bool:
    """Run the Dark Forge pipeline for an issue via the DOT engine."""
    import asyncio  # noqa: PLC0415

    from dark_factory.engine.runner import PipelineStatus  # noqa: PLC0415
    from dark_factory.pipeline.engine import FactoryPipelineEngine  # noqa: PLC0415

    engine = FactoryPipelineEngine(on_event=on_event)
    issue_data = {"number": issue.number, "title": issue.title, "body": issue.body}
    result = asyncio.run(engine.run_forge(
        issue_data, workspace_path, skip_arch_review=skip_arch_review,
    ))
    return result.status == PipelineStatus.COMPLETED


def run_dark_forge(
    issue: IssueInfo,
    workspace_path: str,
    *,
    skip_arch_review: bool = False,
    forge_fn: Callable[[IssueInfo, str], bool] | None = None,
    sentinel_fn: Callable[[str, str], bool] | None = None,
    on_event: Callable[[object], None] | None = None,
) -> bool:
    """Execute Dark Forge with Sentinel gates at lifecycle points."""
    # Pre-forge Sentinel gate
    if not _run_sentinel_gate(workspace_path, "forge-pre", sentinel_fn=sentinel_fn):
        logger.warning("Sentinel pre-forge gate failed for #%d", issue.number)
        return False

    # Run the forge pipeline
    if forge_fn is not None:
        passed = forge_fn(issue, workspace_path)
    else:
        passed = _default_forge(
            issue, workspace_path,
            skip_arch_review=skip_arch_review,
            on_event=on_event,
        )

    # Post-forge Sentinel gate
    if passed and not _run_sentinel_gate(workspace_path, "forge-post", sentinel_fn=sentinel_fn):
        logger.warning("Sentinel post-forge gate failed for #%d", issue.number)
        return False

    return passed


# ── Crucible ──────────────────────────────────────────────────────────


def _default_crucible(workspace: Workspace, issue_number: int) -> CrucibleResult:
    """Run the Crucible test suite via the two-round coordinator."""
    from dark_factory.crucible.coordinator import (  # noqa: PLC0415
        CrucibleCoordinatorConfig,
        run_crucible_pipeline,
        to_crucible_result,
    )

    config = CrucibleCoordinatorConfig(
        pr_number=issue_number,
    )
    two_round = run_crucible_pipeline(workspace, config)
    return to_crucible_result(two_round)


def run_crucible_phase(
    workspace: Workspace,
    issue_number: int,
    *,
    crucible_fn: Callable[[Workspace, int], CrucibleResult] | None = None,
    sentinel_fn: Callable[[str, str], bool] | None = None,
) -> CrucibleResult:
    """Execute Crucible with Sentinel gates at lifecycle points."""
    # Pre-crucible Sentinel gate
    if not _run_sentinel_gate(workspace.path, "crucible-pre", sentinel_fn=sentinel_fn):
        logger.warning("Sentinel pre-crucible gate failed for #%d", issue_number)
        return CrucibleResult(
            verdict=CrucibleVerdict.NO_GO,
            test_results=(),
            screenshots=(),
            logs="",
            phases=(),
            error="Sentinel pre-crucible gate failed",
        )

    fn = crucible_fn or _default_crucible
    result = fn(workspace, issue_number)

    # Post-crucible Sentinel gate (only on GO)
    if result.verdict == CrucibleVerdict.GO:
        if not _run_sentinel_gate(workspace.path, "crucible-post", sentinel_fn=sentinel_fn):
            logger.warning("Sentinel post-crucible gate failed for #%d", issue_number)
            return CrucibleResult(
                verdict=CrucibleVerdict.NO_GO,
                test_results=result.test_results,
                screenshots=result.screenshots,
                logs=result.logs,
                phases=result.phases,
                error="Sentinel post-crucible gate failed",
            )

    return result


# ── Deploy ────────────────────────────────────────────────────────────


def _default_deploy(workspace: Workspace, issue_number: int) -> bool:
    """Push the working branch to the remote."""
    from dark_factory.integrations.shell import git  # noqa: PLC0415

    result = git(["push", "origin", workspace.branch], cwd=workspace.path, timeout=120)
    if result.returncode != 0:
        logger.error("Deploy failed for #%d: %s", issue_number, result.stderr.strip())
        return False
    logger.info("Deployed branch %s for #%d", workspace.branch, issue_number)
    return True


# ── Ouroboros ─────────────────────────────────────────────────────────


def _default_ouroboros(issue: IssueInfo, outcome: CycleOutcome, detail: str) -> None:
    """Capture learnings from the cycle into the knowledge store."""
    try:
        from dark_factory.knowledge.patterns import Pattern, PatternStore  # noqa: PLC0415

        store = PatternStore(".")
        pattern_name = f"auto-cycle-{issue.number}"
        existing = store.get(pattern_name)

        if existing is not None:
            store.update_confidence(pattern_name, success=outcome == CycleOutcome.SUCCESS)
        else:
            store.add(
                Pattern(
                    name=pattern_name,
                    type="auto-cycle",
                    content=f"Issue #{issue.number} ({issue.title}): {outcome.value} — {detail}",
                    confidence=0.7 if outcome == CycleOutcome.SUCCESS else 0.3,
                    tags=["auto-mode", outcome.value],
                )
            )

        store.prune_stale()
        logger.info("Ouroboros: recorded learnings for #%d (%s)", issue.number, outcome.value)
    except Exception:
        logger.exception("Ouroboros: failed to record learnings for #%d", issue.number)


# ── Verdict handling ──────────────────────────────────────────────────


def _handle_needs_live(issue_number: int, crucible_result: CrucibleResult, *, config: AutoModeConfig) -> None:
    """Queue issue for human validation: comment + tag."""
    body = (
        f"**Dark Factory — NEEDS_LIVE verdict**\n\n"
        f"Crucible tests produced a `NEEDS_LIVE` verdict for this issue.\n"
        f"Some tests were skipped and require manual validation in a live environment.\n\n"
        f"- Pass: {crucible_result.pass_count}\n"
        f"- Fail: {crucible_result.fail_count}\n"
        f"- Skip: {crucible_result.skip_count}\n"
        f"- Duration: {crucible_result.duration_s:.1f}s\n"
    )
    try:
        comment_on_issue(issue_number, body, repo=config.repo or None, cwd=config.cwd)
    except GhSafeError:
        logger.exception("Failed to comment on #%d for NEEDS_LIVE", issue_number)
    try:
        add_label(issue_number, LABEL_NEEDS_LIVE, repo=config.repo or None, cwd=config.cwd)
    except GhSafeError:
        logger.exception("Failed to add NEEDS_LIVE label to #%d", issue_number)


def _label_transition(issue_number: int, *, remove: str, add: str, config: AutoModeConfig) -> None:
    """Swap labels on an issue, ignoring errors on removal."""
    repo = config.repo or None
    try:
        remove_label(issue_number, remove, repo=repo, cwd=config.cwd)
    except GhSafeError:
        pass
    try:
        add_label(issue_number, add, repo=repo, cwd=config.cwd)
    except GhSafeError:
        logger.warning("Failed to add label '%s' to #%d", add, issue_number)


# ── GitHub issue lifecycle transitions ────────────────────────────────


def dispatch_to_forge(
    issue_number: int,
    *,
    repo: str = "",
    cwd: str | None = None,
) -> None:
    """Transition issue: queued/backlog -> arch-review + post dispatch comment.

    Parity with bash ``dispatch_issue_on_host()`` in dark-factory.sh.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        remove_label(issue_number, LABEL_QUEUED, repo=repo or None, cwd=cwd)
    except GhSafeError:
        pass
    try:
        add_label(issue_number, LABEL_ARCH_REVIEW, repo=repo or None, cwd=cwd)
    except GhSafeError:
        logger.warning("Failed to add arch-review label to #%d", issue_number)
    try:
        comment_on_issue(
            issue_number,
            f"**Dark Factory -- Dispatched to Architecture Pipeline**\n\n"
            f"- Timestamp: {ts}\n"
            f"- Mode: on-host pipeline\n\n"
            f"Issue is being reviewed by the architecture pipeline.",
            repo=repo or None,
            cwd=cwd,
        )
    except GhSafeError:
        logger.warning("Failed to comment on #%d for arch-review dispatch", issue_number)


def complete_arch_review(
    issue_number: int,
    *,
    passed: bool,
    duration_s: float = 0.0,
    repo: str = "",
    cwd: str | None = None,
) -> None:
    """Transition issue after arch review: arch-review -> arch-approved or failed.

    Parity with bash arch review completion in dark-factory.sh.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        remove_label(issue_number, LABEL_ARCH_REVIEW, repo=repo or None, cwd=cwd)
    except GhSafeError:
        pass
    if passed:
        try:
            add_label(issue_number, LABEL_ARCH_APPROVED, repo=repo or None, cwd=cwd)
        except GhSafeError:
            logger.warning("Failed to add arch-approved label to #%d", issue_number)
        try:
            comment_on_issue(
                issue_number,
                f"**Dark Factory -- Architecture Review Complete**\n\n"
                f"- Verdict: APPROVED\n"
                f"- Duration: {duration_s:.0f}s\n"
                f"- Timestamp: {ts}\n\n"
                f"Engineering pipeline starting.",
                repo=repo or None,
                cwd=cwd,
            )
        except GhSafeError:
            logger.warning("Failed to comment on #%d for arch-review complete", issue_number)
    else:
        try:
            add_label(issue_number, LABEL_FAILED, repo=repo or None, cwd=cwd)
        except GhSafeError:
            logger.warning("Failed to add failed label to #%d", issue_number)


# ── Single-issue cycle ────────────────────────────────────────────────


def _acquire_workspace(repo: str, issue_number: int, *, config: AutoModeConfig) -> Workspace:
    """Acquire a workspace for the issue."""
    if config.acquire_workspace_fn is not None:
        return config.acquire_workspace_fn(repo, issue_number)
    from dark_factory.workspace.manager import acquire_workspace  # noqa: PLC0415

    return acquire_workspace(repo, issue_number)


def run_cycle(
    issue: IssueInfo,
    *,
    config: AutoModeConfig,
    on_event: Callable[[object], None] | None = None,
) -> CycleResult:
    """Process a single issue through the full Dark Forge → Crucible → Deploy → Ouroboros cycle."""
    t0 = time.monotonic()
    issue_num = issue.number
    forge_attempts = 0

    logger.info("Cycle start: #%d %r", issue_num, issue.title)

    # Dispatch to arch review: queued → arch-review + comment
    dispatch_to_forge(issue_num, repo=config.repo, cwd=config.cwd)

    # Acquire workspace (Sentinel gates fire inside acquire_workspace for security files)
    try:
        workspace = _acquire_workspace(config.repo, issue_num, config=config)
    except Exception as exc:
        logger.exception("Failed to acquire workspace for #%d", issue_num)
        _label_transition(issue_num, remove=LABEL_ARCH_REVIEW, add=LABEL_FAILED, config=config)
        return CycleResult(
            issue_number=issue_num,
            outcome=CycleOutcome.ERROR,
            message=f"workspace acquisition failed: {exc}",
            duration_s=time.monotonic() - t0,
        )

    # Dark Forge with NO_GO retry loop
    forge_passed = False
    failure_context = ""
    max_attempts = 1 + config.max_forge_retries

    for attempt in range(1, max_attempts + 1):
        forge_attempts = attempt
        logger.info("Dark Forge attempt %d/%d for #%d", attempt, max_attempts, issue_num)

        forge_passed = run_dark_forge(
            issue, workspace.path, forge_fn=config.forge_fn, sentinel_fn=config.sentinel_fn,
            on_event=on_event,
        )
        if forge_passed:
            break

        failure_context = f"Forge attempt {attempt} failed"
        logger.warning("Dark Forge failed (attempt %d/%d) for #%d", attempt, max_attempts, issue_num)

    # Post-forge lifecycle transition
    forge_elapsed = time.monotonic() - t0
    complete_arch_review(issue_num, passed=forge_passed, duration_s=forge_elapsed, repo=config.repo, cwd=config.cwd)

    if forge_passed:
        # arch-approved → in-progress for the TDD / Crucible phase
        _label_transition(issue_num, remove=LABEL_ARCH_APPROVED, add=LABEL_IN_PROGRESS, config=config)

    if not forge_passed:
        _label_transition(issue_num, remove=LABEL_ARCH_REVIEW, add=LABEL_FAILED, config=config)
        ouroboros_fn = config.ouroboros_fn or _default_ouroboros
        ouroboros_fn(issue, CycleOutcome.FORGE_FAILED, failure_context)
        return CycleResult(
            issue_number=issue_num,
            outcome=CycleOutcome.FORGE_FAILED,
            message=f"Dark Forge failed after {forge_attempts} attempt(s)",
            forge_attempts=forge_attempts,
            duration_s=time.monotonic() - t0,
        )

    # Crucible
    crucible_result = run_crucible_phase(
        workspace, issue_num, crucible_fn=config.crucible_fn, sentinel_fn=config.sentinel_fn,
    )

    if crucible_result.verdict == CrucibleVerdict.NO_GO:
        # Feed failure context back to Dark Forge for another attempt
        failure_context = f"Crucible NO_GO: {crucible_result.error or 'tests failed'}"
        logger.warning("Crucible NO_GO for #%d — retrying Dark Forge", issue_num)

        # One more forge attempt with crucible failure context
        forge_passed = run_dark_forge(
            issue, workspace.path, forge_fn=config.forge_fn, sentinel_fn=config.sentinel_fn,
            on_event=on_event,
        )
        forge_attempts += 1

        if forge_passed:
            crucible_result = run_crucible_phase(
                workspace, issue_num, crucible_fn=config.crucible_fn, sentinel_fn=config.sentinel_fn,
            )

        if crucible_result.verdict == CrucibleVerdict.NO_GO:
            _label_transition(issue_num, remove=LABEL_IN_PROGRESS, add=LABEL_FAILED, config=config)
            ouroboros_fn = config.ouroboros_fn or _default_ouroboros
            ouroboros_fn(issue, CycleOutcome.NO_GO, failure_context)
            return CycleResult(
                issue_number=issue_num,
                outcome=CycleOutcome.NO_GO,
                message=failure_context,
                forge_attempts=forge_attempts,
                duration_s=time.monotonic() - t0,
            )

    if crucible_result.verdict == CrucibleVerdict.NEEDS_LIVE:
        _handle_needs_live(issue_num, crucible_result, config=config)
        _label_transition(issue_num, remove=LABEL_IN_PROGRESS, add=LABEL_NEEDS_LIVE, config=config)
        ouroboros_fn = config.ouroboros_fn or _default_ouroboros
        ouroboros_fn(issue, CycleOutcome.NEEDS_LIVE, "queued for human validation")
        return CycleResult(
            issue_number=issue_num,
            outcome=CycleOutcome.NEEDS_LIVE,
            message="queued for human validation",
            forge_attempts=forge_attempts,
            duration_s=time.monotonic() - t0,
        )

    # Deploy
    deploy_fn = config.deploy_fn or _default_deploy
    deployed = deploy_fn(workspace, issue_num)

    if not deployed:
        _label_transition(issue_num, remove=LABEL_IN_PROGRESS, add=LABEL_FAILED, config=config)
        ouroboros_fn = config.ouroboros_fn or _default_ouroboros
        ouroboros_fn(issue, CycleOutcome.DEPLOY_FAILED, "deploy failed")
        return CycleResult(
            issue_number=issue_num,
            outcome=CycleOutcome.DEPLOY_FAILED,
            message="deploy failed",
            forge_attempts=forge_attempts,
            duration_s=time.monotonic() - t0,
        )

    # Success
    _label_transition(issue_num, remove=LABEL_IN_PROGRESS, add=LABEL_DONE, config=config)

    # Ouroboros — capture learnings
    ouroboros_fn = config.ouroboros_fn or _default_ouroboros
    ouroboros_fn(issue, CycleOutcome.SUCCESS, "full cycle completed")

    elapsed = time.monotonic() - t0
    logger.info("Cycle complete: #%d SUCCESS (%.1fs, %d forge attempts)", issue_num, elapsed, forge_attempts)
    return CycleResult(
        issue_number=issue_num,
        outcome=CycleOutcome.SUCCESS,
        message="full cycle completed",
        forge_attempts=forge_attempts,
        duration_s=elapsed,
    )


# ── Main loop ─────────────────────────────────────────────────────────


def run_auto_mode(config: AutoModeConfig | None = None) -> list[CycleResult]:
    """Run the auto-mode loop: watch for issues → Dark Forge → Crucible → Deploy → Ouroboros → next.

    Returns the list of cycle results when the loop exits (via ``max_cycles``
    or graceful shutdown).
    """
    cfg = config or AutoModeConfig()
    state = AutoModeState()
    _sleep = cfg.sleep_fn or time.sleep

    _install_signal_handlers(state)
    logger.info("Auto mode started (poll_interval=%.1fs, max_cycles=%s)", cfg.poll_interval, cfg.max_cycles)

    while not state.shutdown_requested:
        if cfg.max_cycles is not None and state.cycles_completed >= cfg.max_cycles:
            logger.info("Reached max_cycles=%d — exiting", cfg.max_cycles)
            break

        issue = select_next_issue(repo=cfg.repo or None, cwd=cfg.cwd, state=state.dispatcher)
        if issue is None:
            _sleep(cfg.poll_interval)
            continue

        result = run_cycle(issue, config=cfg)
        state.results.append(result)
        state.cycles_completed += 1

        logger.info(
            "Cycle #%d result: issue=#%d outcome=%s (%.1fs)",
            state.cycles_completed, result.issue_number, result.outcome.value, result.duration_s,
        )

    logger.info("Auto mode stopped after %d cycle(s)", state.cycles_completed)
    return state.results
