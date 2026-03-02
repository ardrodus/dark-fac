"""Test strategy generator — port of generate-test-strategy.sh."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from factory.specs.design_generator import DesignResult
from factory.specs.prd_generator import PRDResult

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT = 300
_STATE_DIR = Path(".dark-factory")
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


def _tup(raw: object) -> tuple[str, ...]:
    if isinstance(raw, list):
        return tuple(str(x) for x in raw)
    return (str(raw),) if raw else ()


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _fmt_analysis(analysis: object) -> str:
    parts: list[str] = []
    for attr, lbl in (("language", "Language"), ("framework", "Framework"),
                      ("test_cmd", "Test cmd"), ("test_dirs", "Test dirs"),
                      ("source_dirs", "Source dirs")):
        val = getattr(analysis, attr, None)
        if val:
            parts.append(f"- **{lbl}:** {val}")
    return "\n".join(parts) or "No analysis available."


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


def _invoke_agent(prompt: str, *, invoke_fn: Callable[[str], str] | None = None) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415
    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, check=True).stdout


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


def _parse_result(raw: str) -> TestStrategyResult:
    text = _strip_fences(raw)
    m = re.search(r"\{", text)
    if m:
        text = text[m.start():]
    d: dict[str, object] = json.loads(text)
    return TestStrategyResult(
        unit_tests=_tup(d.get("unit_tests", [])),
        integration_tests=_tup(d.get("integration_tests", [])),
        e2e_tests=_tup(d.get("e2e_tests", [])),
        fixtures=_tup(d.get("fixtures", [])),
        mocks=_tup(d.get("mocks", [])),
        coverage_targets=_parse_cov(d.get("coverage_targets")),
        affected_tests=_tup(d.get("affected_tests", [])),
        raw_output=raw)


_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("unit_tests", "Unit Tests", "- {}\n"),
    ("integration_tests", "Integration Tests", "- {}\n"),
    ("e2e_tests", "E2E Tests", "- {}\n"),
    ("fixtures", "Fixtures", "- {}\n"),
    ("mocks", "Mocks", "- {}\n"),
    ("affected_tests", "Affected Existing Tests", "- `{}`\n"),
)


def _save_strategy(
    result: TestStrategyResult, num: int | str, *, state_dir: Path | None = None,
) -> Path:
    sd = (state_dir or _STATE_DIR) / "specs" / str(num)
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "test-strategy.md"
    lines = ["# Test Strategy\n\n"]
    for attr, heading, fmt in _SECTIONS:
        items = getattr(result, attr, ())
        if items:
            lines.append(f"## {heading}\n\n")
            lines.extend(fmt.format(t) for t in items)
            lines.append("\n")
    if result.coverage_targets:
        lines.append("## Coverage Targets\n\n")
        lines.extend(f"- **{k}:** {v}%\n" for k, v in result.coverage_targets.items())
        lines.append("\n")
    out.write_text("".join(lines), encoding="utf-8")
    logger.info("Test strategy saved to %s", out)
    return out


def _err(raw: str = "", e: str = "") -> TestStrategyResult:
    return TestStrategyResult(
        unit_tests=(), integration_tests=(), e2e_tests=(),
        fixtures=(), mocks=(), coverage_targets={},
        raw_output=raw, errors=(e,) if e else ())


def _extract_inum(prd: PRDResult) -> int | str:
    m = re.search(r"#(\d+)", prd.title)
    return int(m.group(1)) if m else 0


def generate_test_strategy(
    prd: PRDResult,
    design: DesignResult,
    analysis: object,
    *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
    issue_number: int | str = 0,
) -> TestStrategyResult:
    """Generate a test strategy from *prd*, *design*, and *analysis*."""
    prompt = _build_prompt(prd, design, _fmt_analysis(analysis))
    try:
        raw = _invoke_agent(prompt, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Test strategy agent failed: %s", exc)
        return _err(e=str(exc))
    try:
        result = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Failed to parse test strategy: %s", exc)
        return _err(raw, f"parse: {exc}")
    num = issue_number or _extract_inum(prd)
    _save_strategy(result, num, state_dir=state_dir)
    return result
