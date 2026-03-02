"""Design document generator — produce technical design from PRD + codebase analysis.

Ports generate-design.sh.  Invokes Claude to produce a DesignResult with
architecture decisions, component changes, data model changes, API changes,
and risks.  References existing codebase patterns from the analysis.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from factory.specs.prd_generator import PRDResult

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT = 300
_STATE_DIR = Path(".dark-factory")


@dataclass(frozen=True, slots=True)
class DesignResult:
    """Structured output of technical design generation."""
    architecture_decisions: tuple[str, ...]
    component_changes: tuple[str, ...]
    data_model_changes: tuple[str, ...]
    api_changes: tuple[str, ...]
    risks: tuple[str, ...]
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _tup(raw: object) -> tuple[str, ...]:
    """Coerce list-or-scalar to tuple of strings."""
    if isinstance(raw, list):
        return tuple(str(x) for x in raw)
    return (str(raw),) if raw else ()


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _build_prompt(
    prd: PRDResult,
    analysis_summary: str,
) -> str:
    stories = "\n".join(
        f"- **{s.id}**: {s.title} (priority={s.priority})"
        for s in prd.user_stories
    )
    nfrs = ", ".join(prd.non_functional_requirements) or "None"
    return (
        "You are a Design Engineer for Dark Factory.\n\n## Task\n\n"
        "Produce a technical design document for the following PRD.\n"
        "Reference existing codebase patterns from the analysis.\n\n"
        f"## PRD\n\n**Title:** {prd.title}\n\n{prd.description}\n\n"
        f"### User Stories\n\n{stories}\n\n"
        f"### Non-Functional Requirements\n\n{nfrs}\n\n"
        f"## Codebase Analysis\n\n{analysis_summary}\n\n"
        "## Rules\n\n"
        "1. Architecture decisions must justify trade-offs.\n"
        "2. Component changes reference existing files/modules.\n"
        "3. Data model changes include migration notes.\n"
        "4. API changes specify method, path, request/response shape.\n"
        "5. Risks include likelihood (high/medium/low) and mitigation.\n\n"
        "## Output\n\nJSON only (no fences):\n\n"
        '{"architecture_decisions":["decision"],'
        '"component_changes":["change"],'
        '"data_model_changes":["change"],'
        '"api_changes":["change"],'
        '"risks":["risk"]}'
    )


def _invoke_agent(
    prompt: str, *, invoke_fn: Callable[[str], str] | None = None,
) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415
    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, check=True,
    ).stdout


def _parse_result(raw: str) -> DesignResult:
    text = _strip_fences(raw)
    m = re.search(r"\{", text)
    if m:
        text = text[m.start():]
    d: dict[str, object] = json.loads(text)
    return DesignResult(
        architecture_decisions=_tup(d.get("architecture_decisions", [])),
        component_changes=_tup(d.get("component_changes", [])),
        data_model_changes=_tup(d.get("data_model_changes", [])),
        api_changes=_tup(d.get("api_changes", [])),
        risks=_tup(d.get("risks", [])),
        raw_output=raw,
    )


def _format_analysis(analysis: object) -> str:
    """Build a concise analysis summary from an AnalysisResult."""
    parts: list[str] = []
    for attr, label in (
        ("language", "Language"), ("framework", "Framework"),
        ("detected_strategy", "Strategy"), ("build_cmd", "Build"),
        ("test_cmd", "Test"), ("source_dirs", "Sources"),
        ("test_dirs", "Tests"),
    ):
        val = getattr(analysis, attr, None)
        if val:
            parts.append(f"- **{label}:** {val}")
    return "\n".join(parts) or "No analysis available."


def _save_design(
    result: DesignResult, num: int | str, *, state_dir: Path | None = None,
) -> Path:
    sd = (state_dir or _STATE_DIR) / "specs" / str(num)
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "design.md"
    lines = ["# Technical Design\n"]
    if result.architecture_decisions:
        lines.append("## Architecture Decisions\n")
        lines.extend(f"- {d}\n" for d in result.architecture_decisions)
        lines.append("\n")
    if result.component_changes:
        lines.append("## Component Changes\n")
        lines.extend(f"- {c}\n" for c in result.component_changes)
        lines.append("\n")
    if result.data_model_changes:
        lines.append("## Data Model Changes\n")
        lines.extend(f"- {c}\n" for c in result.data_model_changes)
        lines.append("\n")
    if result.api_changes:
        lines.append("## API Changes\n")
        lines.extend(f"- {c}\n" for c in result.api_changes)
        lines.append("\n")
    if result.risks:
        lines.append("## Risks\n")
        lines.extend(f"- {r}\n" for r in result.risks)
        lines.append("\n")
    out.write_text("".join(lines), encoding="utf-8")
    logger.info("Design saved to %s", out)
    return out


def _err(raw: str = "", e: str = "") -> DesignResult:
    return DesignResult(
        architecture_decisions=(), component_changes=(),
        data_model_changes=(), api_changes=(), risks=(),
        raw_output=raw, errors=(e,) if e else (),
    )


def generate_design(
    prd: PRDResult,
    analysis: object,
    *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
    issue_number: int | str = 0,
) -> DesignResult:
    """Generate a technical design document from *prd* and *analysis*."""
    summary = _format_analysis(analysis)
    prompt = _build_prompt(prd, summary)
    try:
        raw = _invoke_agent(prompt, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Design agent failed: %s", exc)
        return _err(e=str(exc))
    try:
        result = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Failed to parse design: %s", exc)
        return _err(raw, f"parse: {exc}")
    num = issue_number or _extract_issue_number(prd)
    _save_design(result, num, state_dir=state_dir)
    return result


def _extract_issue_number(prd: PRDResult) -> int | str:
    """Try to extract issue number from PRD title (e.g. '#42')."""
    m = re.search(r"#(\d+)", prd.title)
    return int(m.group(1)) if m else 0
