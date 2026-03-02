"""Test strategy generator — port of generate-test-strategy.sh."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from dark_factory.specs.base import (
    extract_json,
    format_analysis,
    run_generate,
    save_artifact,
    tup,
)
from dark_factory.specs.design_generator import DesignResult
from dark_factory.specs.prd_generator import PRDResult

_DEF_COV: dict[str, float] = {"unit": 80.0, "integration": 60.0, "e2e": 40.0, "overall": 70.0}


@dataclass(frozen=True, slots=True)
class TestStrategyResult:
    """Structured output of test strategy generation."""
    unit_tests: tuple[str, ...]
    integration_tests: tuple[str, ...]
    e2e_tests: tuple[str, ...]
    fixtures: tuple[str, ...]
    mocks: tuple[str, ...]
    coverage_targets: dict[str, float]
    affected_tests: tuple[str, ...] = field(default_factory=tuple)
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _build_prompt(prd: PRDResult, design: DesignResult, asummary: str) -> str:
    stories = "\n".join(
        f"- **{s.id}**: {s.title} — AC: {', '.join(s.acceptance_criteria[:3])}"
        for s in prd.user_stories)
    decs = "\n".join(f"- {d}" for d in design.architecture_decisions[:10])
    comps = "\n".join(f"- {c}" for c in design.component_changes[:10])
    risks = "\n".join(f"- {r}" for r in design.risks[:5])
    return (
        "You are a Test Strategy Engineer for Dark Factory.\n\n## Task\n\n"
        "Produce a test strategy: what to test, how, fixtures/mocks needed.\n\n"
        f"## PRD\n\n**Title:** {prd.title}\n\n{prd.description}\n\n"
        f"### User Stories\n\n{stories}\n\n"
        f"## Design\n\n### Decisions\n{decs}\n\n### Components\n{comps}\n\n"
        f"### Risks\n{risks}\n\n## Analysis\n\n{asummary}\n\n## Rules\n\n"
        "1. Unit tests: one per function/method, name the function.\n"
        "2. Integration tests: cross-component flows with setup.\n"
        "3. E2E tests: full user-facing scenarios.\n"
        "4. Fixtures: concrete data (DB seeds, files, env vars).\n"
        "5. Mocks: external services, APIs, time, randomness.\n"
        "6. Coverage targets: percentage per category.\n"
        "7. Identify existing test files that may need updating.\n"
        "8. Be SPECIFIC — name exact functions, classes, files.\n\n"
        "## Output\n\nJSON only (no fences):\n\n"
        '{"unit_tests":["desc"],"integration_tests":["desc"],"e2e_tests":["desc"],'
        '"fixtures":["desc"],"mocks":["desc"],'
        '"coverage_targets":{"unit":90,"integration":70,"e2e":50,"overall":80},'
        '"affected_tests":["path/to/test.py"]}')


def _parse_cov(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return dict(_DEF_COV)
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            out[str(k)] = 0.0
    return out or dict(_DEF_COV)


_MD_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("unit_tests", "Unit Tests", "- {}\n"),
    ("integration_tests", "Integration Tests", "- {}\n"),
    ("e2e_tests", "E2E Tests", "- {}\n"),
    ("fixtures", "Fixtures", "- {}\n"),
    ("mocks", "Mocks", "- {}\n"),
    ("affected_tests", "Affected Existing Tests", "- `{}`\n"),
)


def _process(raw: str, num: int | str, state_dir: Path | None) -> TestStrategyResult:
    d = extract_json(raw)
    result = TestStrategyResult(
        unit_tests=tup(d.get("unit_tests", [])),
        integration_tests=tup(d.get("integration_tests", [])),
        e2e_tests=tup(d.get("e2e_tests", [])),
        fixtures=tup(d.get("fixtures", [])),
        mocks=tup(d.get("mocks", [])),
        coverage_targets=_parse_cov(d.get("coverage_targets")),
        affected_tests=tup(d.get("affected_tests", [])), raw_output=raw,
    )
    lines = ["# Test Strategy\n\n"]
    for attr, heading, fmt in _MD_SECTIONS:
        items = getattr(result, attr, ())
        if items:
            lines.append(f"## {heading}\n\n")
            lines.extend(fmt.format(t) for t in items)
            lines.append("\n")
    if result.coverage_targets:
        lines.append("## Coverage Targets\n\n")
        lines.extend(f"- **{k}:** {v}%\n" for k, v in result.coverage_targets.items())
        lines.append("\n")
    save_artifact("".join(lines), "test-strategy.md", num, state_dir=state_dir)
    return result


def _err(raw: str = "", e: str = "") -> TestStrategyResult:
    return TestStrategyResult(
        unit_tests=(), integration_tests=(), e2e_tests=(),
        fixtures=(), mocks=(), coverage_targets={},
        raw_output=raw, errors=(e,) if e else ())


def generate_test_strategy(
    prd: PRDResult, design: DesignResult, analysis: object, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None, issue_number: int | str = 0,
) -> TestStrategyResult:
    """Generate a test strategy from *prd*, *design*, and *analysis*."""
    m = re.search(r"#(\d+)", prd.title)
    num = issue_number or (int(m.group(1)) if m else 0)
    return run_generate(
        "Test strategy", _build_prompt(prd, design, format_analysis(analysis)),
        lambda raw: _process(raw, num, state_dir),
        _err, invoke_fn=invoke_fn,
    )
