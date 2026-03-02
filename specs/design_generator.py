"""Design document generator — produce technical design from PRD + codebase analysis."""
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
from dark_factory.specs.prd_generator import PRDResult


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


def _build_prompt(prd: PRDResult, summary: str) -> str:
    stories = "\n".join(
        f"- **{s.id}**: {s.title} (priority={s.priority})" for s in prd.user_stories)
    nfrs = ", ".join(prd.non_functional_requirements) or "None"
    return (
        "You are a Design Engineer for Dark Factory.\n\n## Task\n\n"
        "Produce a technical design document for the following PRD.\n"
        "Reference existing codebase patterns from the analysis.\n\n"
        f"## PRD\n\n**Title:** {prd.title}\n\n{prd.description}\n\n"
        f"### User Stories\n\n{stories}\n\n### Non-Functional Requirements\n\n{nfrs}\n\n"
        f"## Codebase Analysis\n\n{summary}\n\n## Rules\n\n"
        "1. Architecture decisions must justify trade-offs.\n"
        "2. Component changes reference existing files/modules.\n"
        "3. Data model changes include migration notes.\n"
        "4. API changes specify method, path, request/response shape.\n"
        "5. Risks include likelihood (high/medium/low) and mitigation.\n\n"
        "## Output\n\nJSON only (no fences):\n\n"
        '{"architecture_decisions":["decision"],"component_changes":["change"],'
        '"data_model_changes":["change"],"api_changes":["change"],"risks":["risk"]}'
    )


_SECTIONS = (
    ("Architecture Decisions", "architecture_decisions"),
    ("Component Changes", "component_changes"),
    ("Data Model Changes", "data_model_changes"),
    ("API Changes", "api_changes"),
    ("Risks", "risks"),
)


def _process(raw: str, num: int | str, state_dir: Path | None) -> DesignResult:
    d = extract_json(raw)
    result = DesignResult(
        architecture_decisions=tup(d.get("architecture_decisions", [])),
        component_changes=tup(d.get("component_changes", [])),
        data_model_changes=tup(d.get("data_model_changes", [])),
        api_changes=tup(d.get("api_changes", [])),
        risks=tup(d.get("risks", [])), raw_output=raw,
    )
    lines = ["# Technical Design\n"]
    for heading, attr in _SECTIONS:
        items = getattr(result, attr)
        if items:
            lines.append(f"\n## {heading}\n")
            lines.extend(f"- {i}\n" for i in items)
    save_artifact("".join(lines), "design.md", num, state_dir=state_dir)
    return result


def _err(raw: str = "", e: str = "") -> DesignResult:
    return DesignResult(
        architecture_decisions=(), component_changes=(),
        data_model_changes=(), api_changes=(), risks=(),
        raw_output=raw, errors=(e,) if e else (),
    )


def generate_design(
    prd: PRDResult, analysis: object, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None, issue_number: int | str = 0,
) -> DesignResult:
    """Generate a technical design document from *prd* and *analysis*."""
    summary = format_analysis(analysis, (
        ("language", "Language"), ("framework", "Framework"),
        ("detected_strategy", "Strategy"), ("build_cmd", "Build"),
        ("test_cmd", "Test"), ("source_dirs", "Sources"), ("test_dirs", "Tests"),
    ))
    m = re.search(r"#(\d+)", prd.title)
    num = issue_number or (int(m.group(1)) if m else 0)
    return run_generate(
        "Design", _build_prompt(prd, summary),
        lambda raw: _process(raw, num, state_dir),
        _err, invoke_fn=invoke_fn,
    )
