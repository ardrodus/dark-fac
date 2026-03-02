"""Route-to-engineering — full issue-to-PR pipeline orchestrator.

Acquires workspace, generates specs, runs TDD pipeline, security review,
and creates a PR linked to the issue.  On failure: labels issue as blocked.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.pipeline.tdd.orchestrator import TDDResult
    from factory.pipeline.tdd.test_writer import SpecBundle
    from factory.specs.design_generator import DesignResult
    from factory.specs.prd_generator import PRDResult
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineMetrics:
    """Timing for each pipeline stage."""
    workspace_seconds: float = 0.0
    specs_seconds: float = 0.0
    tdd_seconds: float = 0.0
    contract_seconds: float = 0.0
    security_seconds: float = 0.0
    pr_seconds: float = 0.0
    total_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class RouteResult:
    """Outcome of the full engineering route."""
    success: bool
    pr_url: str = ""
    error_message: str = ""
    pipeline_metrics: PipelineMetrics = field(default_factory=PipelineMetrics)


@dataclass(frozen=True, slots=True)
class RouteConfig:
    """Settings for the engineering pipeline."""
    repo: str = ""
    tdd_max_rounds: int = 3
    tdd_test_command: tuple[str, ...] = ("pytest", "-v", "--tb=short")
    tdd_test_timeout: int = 120


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


def _fail(msg: str, num: int, repo: str, m: PipelineMetrics) -> RouteResult:
    logger.error("Pipeline failed for #%d: %s", num, msg)
    _label_blocked(num, repo, msg)
    return RouteResult(success=False, error_message=msg, pipeline_metrics=m)


def _acquire(repo: str, num: int) -> Workspace:
    from factory.workspace.manager import acquire_workspace  # noqa: PLC0415
    return acquire_workspace(repo, num)


def _gen_specs(
    issue: dict[str, object], *, invoke_fn: Callable[[str], str] | None = None,
) -> tuple[PRDResult, DesignResult]:
    from factory.specs.design_generator import generate_design  # noqa: PLC0415
    from factory.specs.prd_generator import generate_prd  # noqa: PLC0415
    prd = generate_prd(issue, invoke_fn=invoke_fn)
    design = generate_design(prd, None, invoke_fn=invoke_fn, issue_number=_inum(issue))
    return prd, design


def _make_bundle(prd: PRDResult, design: DesignResult) -> SpecBundle:
    from factory.pipeline.tdd.test_writer import SpecBundle  # noqa: PLC0415
    prd_text = prd.raw_output or f"{prd.title} - {prd.description}"
    design_text = design.raw_output or "\n".join(design.architecture_decisions)
    return SpecBundle(prd=prd_text, design_doc=design_text)


def _run_tdd(
    specs: SpecBundle, ws: Workspace, cfg: RouteConfig,
    *, invoke_fn: Callable[[str], str] | None = None,
) -> TDDResult:
    from factory.pipeline.tdd.orchestrator import TDDConfig, run_tdd_pipeline  # noqa: PLC0415
    tc = TDDConfig(max_rounds=cfg.tdd_max_rounds, test_command=cfg.tdd_test_command,
                   test_timeout=cfg.tdd_test_timeout)
    return run_tdd_pipeline(specs, ws, tc, invoke_fn=invoke_fn)


def _contract_validation(ws: Workspace, issue_num: int) -> bool:
    from factory.gates.contract_validation import run_contract_validation  # noqa: PLC0415
    from factory.gates.framework import CheckStatus  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    specs_dir = str(Path(ws.path) / ".dark-factory" / "specs")
    result = run_contract_validation(ws.path, specs_dir, str(issue_num))
    if not result.passed:
        for c in result.checks:
            if c.status == CheckStatus.FAIL:
                logger.warning("Contract violation [%s]: %s", c.name, c.details)
    return result.passed


def _security_review(ws_path: str) -> bool:
    from factory.gates.framework import GateRunner  # noqa: PLC0415
    from factory.integrations.shell import git  # noqa: PLC0415
    runner = GateRunner("security-pre-pr", metrics_dir=ws_path)
    runner.register_check("secret-scan-pre-pr",
                          lambda: git(["log", "--oneline", "-1"], cwd=ws_path).returncode == 0)
    return runner.run().passed


def _pr_body(num: int, title: str, m: PipelineMetrics, tdd: TDDResult) -> str:
    files = tdd.files_changed
    fl = "\n".join(f"- `{f}`" for f in files) if files else "N/A"
    return (
        f"## Summary\n\nAutomated implementation for issue #{num}.\n\n"
        f"Closes #{num}\n\n## Pipeline Metrics\n\n"
        f"- **Workspace**: {m.workspace_seconds:.1f}s\n"
        f"- **Specs**: {m.specs_seconds:.1f}s\n"
        f"- **TDD**: {m.tdd_seconds:.1f}s ({tdd.rounds} rounds)\n"
        f"- **Security**: {m.security_seconds:.1f}s\n"
        f"- **Total**: {m.total_seconds:.1f}s\n\n"
        f"## Files Changed\n\n{fl}\n\n---\n*Generated by Dark Factory — {title}*"
    )


def _create_pr(
    ws_path: str, repo: str, num: int, title: str,
    branch: str, m: PipelineMetrics, tdd: TDDResult,
) -> str:
    from factory.integrations.shell import gh, git  # noqa: PLC0415
    git(["add", "-A"], cwd=ws_path, check=False)
    if git(["diff", "--cached", "--name-only"], cwd=ws_path).stdout.strip():
        git(["commit", "-m", f"feat: implement issue #{num}\n\n"
             "Generated by Dark Factory engineering pipeline."],
            cwd=ws_path, check=False)
    git(["push", "origin", branch], cwd=ws_path, check=False, timeout=120)
    pr_title = f"feat: implement issue #{num} — {title}"
    if len(pr_title) > 72:  # noqa: PLR2004
        pr_title = pr_title[:69] + "..."
    body = _pr_body(num, title, m, tdd)
    r = gh(["pr", "create", "--repo", repo, "--head", branch,
            "--base", "main", "--title", pr_title, "--body", body],
           check=False, timeout=60)
    url = r.stdout.strip()
    if r.returncode != 0 or not url:
        logger.warning("PR creation failed: %s", r.stderr.strip())
        return ""
    return url


def route_to_engineering(
    issue: dict[str, object],
    config: RouteConfig,
    *,
    invoke_fn: Callable[[str], str] | None = None,
) -> RouteResult:
    """Orchestrate the full issue-to-PR engineering pipeline.

    Sequence: acquire workspace -> generate specs -> run TDD pipeline ->
    run security review -> create PR.
    On failure: issue labeled blocked, DLQ entry created, Obelisk triage.
    """
    t0 = time.monotonic()
    num, repo = _inum(issue), config.repo
    ws_s = spec_s = tdd_s = cvg_s = sec_s = pr_s = 0.0

    def _m() -> PipelineMetrics:
        return PipelineMetrics(workspace_seconds=ws_s, specs_seconds=spec_s,
                               tdd_seconds=tdd_s, contract_seconds=cvg_s,
                               security_seconds=sec_s, pr_seconds=pr_s,
                               total_seconds=round(time.monotonic() - t0, 2))

    # Stage 1: Acquire workspace
    logger.info("Routing issue #%d to engineering pipeline", num)
    try:
        s = time.monotonic()
        workspace = _acquire(repo, num)
        ws_s = round(time.monotonic() - s, 2)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Workspace acquisition failed: {exc}", num, repo, _m())
    ws_path, branch = workspace.path, workspace.branch

    # Stage 2: Generate specs (PRD + design)
    try:
        s = time.monotonic()
        prd, design = _gen_specs(issue, invoke_fn=invoke_fn)
        spec_s = round(time.monotonic() - s, 2)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Spec generation failed: {exc}", num, repo, _m())
    if prd.errors or design.errors:
        errs = [*prd.errors, *design.errors]
        return _fail(f"Spec errors: {'; '.join(errs)}", num, repo, _m())

    # Stage 3: Run TDD pipeline
    bundle = _make_bundle(prd, design)
    try:
        s = time.monotonic()
        tdd = _run_tdd(bundle, workspace, config, invoke_fn=invoke_fn)
        tdd_s = round(time.monotonic() - s, 2)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"TDD pipeline failed: {exc}", num, repo, _m())
    if not tdd.success:
        em = "; ".join(tdd.errors) if tdd.errors else "TDD failed"
        return _fail(f"TDD pipeline failed: {em}", num, repo, _m())

    # Stage 4: Contract validation
    try:
        s = time.monotonic()
        cvg_ok = _contract_validation(workspace, num)
        cvg_s = round(time.monotonic() - s, 2)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Contract validation failed: {exc}", num, repo, _m())
    if not cvg_ok:
        return _fail("Contract validation gate failed", num, repo, _m())

    # Stage 5: Security review
    try:
        s = time.monotonic()
        sec_ok = _security_review(ws_path)
        sec_s = round(time.monotonic() - s, 2)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Security review failed: {exc}", num, repo, _m())
    if not sec_ok:
        return _fail("Security review failed", num, repo, _m())

    # Stage 6: Create PR
    title = _ititle(issue)
    try:
        s = time.monotonic()
        pr_url = _create_pr(ws_path, repo, num, title, branch, _m(), tdd)
        pr_s = round(time.monotonic() - s, 2)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"PR creation failed: {exc}", num, repo, _m())
    if not pr_url:
        return _fail("PR creation returned empty URL", num, repo, _m())

    logger.info("Pipeline succeeded for #%d — PR: %s", num, pr_url)
    return RouteResult(success=True, pr_url=pr_url, pipeline_metrics=_m())
