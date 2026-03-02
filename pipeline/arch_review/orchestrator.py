"""Architecture review pipeline orchestrator.

Runs specialist agents in parallel, feeds into SA Lead, caches results.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from dark_factory.pipeline.arch_review.sa_lead import ArchReviewVerdict, run_sa_lead
from dark_factory.pipeline.arch_review.specialists import (
    ALL_SPECIALISTS,
    Specialist,
    SpecialistResult,
    run_specialist,
)

logger = logging.getLogger(__name__)
_DEFAULT_TIMEOUT = 120
_DEFAULT_WORKERS = 4


@dataclass(frozen=True, slots=True)
class ArchReviewConfig:
    """Settings for the architecture review pipeline."""
    specialist_timeout: int = _DEFAULT_TIMEOUT
    max_workers: int = _DEFAULT_WORKERS
    cache_dir: str = ""
    repo: str = ""


@dataclass(frozen=True, slots=True)
class ReviewMetrics:
    """Pipeline timing and counts."""
    total_seconds: float = 0.0
    specialist_count: int = 0
    succeeded: int = 0
    failed: int = 0
    verdict: str = ""


def _inum(issue: dict[str, object]) -> int:
    raw = issue.get("number", 0)
    return int(raw) if isinstance(raw, (int, float, str)) else 0


def _cache_dir(issue: dict[str, object], config: ArchReviewConfig) -> Path:
    if config.cache_dir:
        base = Path(config.cache_dir)
    else:
        from dark_factory.core.config_manager import resolve_config_dir  # noqa: PLC0415

        base = resolve_config_dir()
    return base / "reviews" / str(_inum(issue))


def _cache_specialist(directory: Path, r: SpecialistResult) -> None:
    data = {
        "agent_name": r.agent_name,
        "findings": list(r.findings),
        "risk_level": r.risk_level,
        "recommendations": list(r.recommendations),
        "approval": r.approval,
        "errors": list(r.errors),
    }
    (directory / f"{r.agent_name}.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8",
    )


def _cache_verdict(directory: Path, v: ArchReviewVerdict) -> None:
    ra = v.risk_assessment
    data = {
        "verdict": v.verdict.value,
        "summary": v.summary,
        "blocking_findings": list(v.blocking_findings),
        "conditions": list(v.conditions),
        "risk_assessment": {
            "overall_level": ra.overall_level,
            "critical_count": ra.critical_count,
            "high_count": ra.high_count,
            "risk_areas": list(ra.risk_areas),
        },
    }
    (directory / "verdict.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8",
    )


def _cache_results(
    issue: dict[str, object],
    config: ArchReviewConfig,
    results: list[SpecialistResult],
    verdict: ArchReviewVerdict,
) -> None:
    try:
        d = _cache_dir(issue, config)
        d.mkdir(parents=True, exist_ok=True)
        for r in results:
            _cache_specialist(d, r)
        _cache_verdict(d, verdict)
        logger.info("Cached review results to %s", d)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to cache review results: %s", exc)


def _error_result(name: str, error: str) -> SpecialistResult:
    return SpecialistResult(
        agent_name=name, findings=(), risk_level="medium",
        recommendations=(), approval=False, errors=(error,),
    )


def _run_one(
    agent: Specialist, issue: dict[str, object], ctx: dict[str, object],
    *, invoke_fn: Callable[[str], str] | None = None,
    thread_semaphore: threading.Semaphore | None = None,
) -> SpecialistResult:
    """Run a single specialist, catching all errors."""
    if thread_semaphore is not None:
        thread_semaphore.acquire()
    try:
        return run_specialist(agent, issue, ctx, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Specialist %s failed: %s", agent.name, exc)
        return _error_result(agent.name, f"error:{exc}")
    finally:
        if thread_semaphore is not None:
            thread_semaphore.release()


def _run_parallel(
    specialists: tuple[Specialist, ...],
    issue: dict[str, object],
    ctx: dict[str, object],
    config: ArchReviewConfig,
    *,
    invoke_fn: Callable[[str], str] | None = None,
    thread_semaphore: threading.Semaphore | None = None,
) -> list[SpecialistResult]:
    """Run all specialists in parallel with configurable timeout."""
    results: list[SpecialistResult] = []
    fmap: dict[Future[SpecialistResult], Specialist] = {}
    with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
        for agent in specialists:
            f = pool.submit(
                _run_one, agent, issue, ctx,
                invoke_fn=invoke_fn, thread_semaphore=thread_semaphore,
            )
            fmap[f] = agent
        done, not_done = wait(fmap, timeout=config.specialist_timeout)
    for f in done:
        try:
            results.append(f.result())
        except Exception as exc:  # noqa: BLE001
            results.append(_error_result(fmap[f].name, f"error:{exc}"))
    for f in not_done:
        f.cancel()
        a = fmap[f]
        logger.warning("Specialist %s timed out after %ds", a.name, config.specialist_timeout)
        results.append(_error_result(a.name, "timeout"))
    return results


def run_arch_review(
    issue: dict[str, object],
    config: ArchReviewConfig | None = None,
    *,
    specialists: tuple[Specialist, ...] | None = None,
    context: dict[str, object] | None = None,
    invoke_fn: Callable[[str], str] | None = None,
) -> ArchReviewVerdict:
    """Orchestrate the full architecture review pipeline.

    Runs specialists in parallel, feeds into SA Lead, caches results.
    Failed specialists are logged but don't block the pipeline.
    """
    if config is None:
        config = ArchReviewConfig()
    if specialists is None:
        specialists = ALL_SPECIALISTS
    ctx = context or {}
    t0 = time.monotonic()
    num = _inum(issue)
    logger.info("Starting arch review #%d (%d specialists)", num, len(specialists))
    results = _run_parallel(specialists, issue, ctx, config, invoke_fn=invoke_fn)
    verdict = run_sa_lead(results, issue, repo=config.repo)
    elapsed = round(time.monotonic() - t0, 2)
    ok = sum(1 for r in results if not r.errors)
    metrics = ReviewMetrics(
        total_seconds=elapsed, specialist_count=len(results),
        succeeded=ok, failed=len(results) - ok, verdict=verdict.verdict.value,
    )
    logger.info(
        "Arch review #%d: %s in %.1fs (%d/%d ok)",
        num, metrics.verdict, metrics.total_seconds,
        metrics.succeeded, metrics.specialist_count,
    )
    _cache_results(issue, config, results, verdict)
    return verdict
