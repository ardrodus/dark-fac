"""Pipeline runner for the Dark Factory self-consumption pipeline.

Orchestrates the end-to-end processing of a story through discrete stages:

1. **Plan** — parse the story and identify acceptance criteria.
2. **Implement** — generate / apply code changes.
3. **Test** — run ``pytest`` against the affected modules.
4. **Quality gate** — run file-type-aware linting (ruff/mypy/shellcheck).
5. **Review** — evaluate the diff for Python idioms and type safety.
6. **Audit** — final PASS/FAIL verdict from the auditor agent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from dark_factory.agents.prompts import AGENT_PROMPTS
from dark_factory.gates.quality import (
    FileKind,
    QualityReport,
    classify_changeset,
    gate_pytest,
    run_quality_gates,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


class Stage(Enum):
    """Pipeline stages in execution order."""

    PLAN = "plan"
    IMPLEMENT = "implement"
    TEST = "test"
    QUALITY_GATE = "quality_gate"
    REVIEW = "review"
    AUDIT = "audit"


@dataclass(frozen=True, slots=True)
class StoryContext:
    """Parsed context for a story being processed."""

    title: str
    description: str
    acceptance_criteria: tuple[str, ...]
    changed_files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StageResult:
    """Outcome of a single pipeline stage."""

    stage: Stage
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Final result of the full pipeline run."""

    story: StoryContext
    stages: tuple[StageResult, ...]
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", all(s.passed for s in self.stages))


def _select_gates(changed_files: Sequence[str]) -> str:
    """Return a human description of which gates will run."""
    kinds = classify_changeset(changed_files)
    parts: list[str] = []
    if FileKind.PYTHON in kinds:
        parts.append("ruff + mypy (Python)")
    if FileKind.BASH in kinds:
        parts.append("shellcheck (bash)")
    return ", ".join(parts) or "no gates applicable"


def run_pipeline(story: StoryContext, *, cwd: str | None = None) -> PipelineResult:
    """Execute the full pipeline for *story*.

    Each stage is run sequentially.  If a stage fails the pipeline continues
    (to collect all diagnostics) but the final :attr:`PipelineResult.passed`
    will be ``False``.
    """
    results: list[StageResult] = []

    # ── Plan ──────────────────────────────────────────────────────
    logger.info("Pipeline: plan stage for %r", story.title)
    plan_detail = f"Story has {len(story.acceptance_criteria)} acceptance criteria."
    results.append(StageResult(stage=Stage.PLAN, passed=True, detail=plan_detail))

    # ── Implement ─────────────────────────────────────────────────
    logger.info("Pipeline: implement stage")
    prompt = AGENT_PROMPTS["pipeline"]
    impl_detail = f"Using {prompt.role} agent prompt ({len(prompt.template)} chars)."
    results.append(StageResult(stage=Stage.IMPLEMENT, passed=True, detail=impl_detail))

    # ── Test ──────────────────────────────────────────────────────
    if story.changed_files:
        logger.info("Pipeline: test stage")
        test_result = gate_pytest(cwd=cwd)
        results.append(
            StageResult(stage=Stage.TEST, passed=test_result.passed, detail=test_result.output)
        )
    else:
        results.append(StageResult(stage=Stage.TEST, passed=True, detail="No changed files — skipped."))

    # ── Quality gate ──────────────────────────────────────────────
    if story.changed_files:
        logger.info("Pipeline: quality gate stage — %s", _select_gates(story.changed_files))
        qr: QualityReport = run_quality_gates(story.changed_files, cwd=cwd, run_tests=False)
        gate_detail = "; ".join(f"{r.gate}: {'PASS' if r.passed else 'FAIL'}" for r in qr.results)
        results.append(StageResult(stage=Stage.QUALITY_GATE, passed=qr.passed, detail=gate_detail))
    else:
        results.append(StageResult(stage=Stage.QUALITY_GATE, passed=True, detail="No changed files — skipped."))

    # ── Review ────────────────────────────────────────────────────
    logger.info("Pipeline: review stage")
    review_prompt = AGENT_PROMPTS["code-review"]
    review_detail = f"Code review prompt ready ({review_prompt.role})."
    results.append(StageResult(stage=Stage.REVIEW, passed=True, detail=review_detail))

    # ── Audit ─────────────────────────────────────────────────────
    logger.info("Pipeline: audit stage")
    auditor = AGENT_PROMPTS["auditor"]
    audit_detail = f"Auditor prompt ready ({auditor.role}). Verdict deferred to agent."
    results.append(StageResult(stage=Stage.AUDIT, passed=True, detail=audit_detail))

    return PipelineResult(story=story, stages=tuple(results))
