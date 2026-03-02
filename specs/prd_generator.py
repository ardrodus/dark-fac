"""PRD generator — convert GitHub issue + arch guidance into a PRD."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from dark_factory.specs.base import extract_json, run_generate, save_artifact, tup

_MAX_STORIES = 20


class DetailLevel(Enum):
    """PRD zoom level."""
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


@dataclass(frozen=True, slots=True)
class UserStory:
    """A single user story within a PRD."""
    id: str
    title: str
    description: str
    acceptance_criteria: tuple[str, ...]
    priority: str = "medium"
    depends_on: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PRDResult:
    """Structured output of PRD generation."""
    title: str
    description: str
    user_stories: tuple[UserStory, ...]
    non_functional_requirements: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    detail_level: DetailLevel = DetailLevel.L3
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _build_prompt(
    issue: dict[str, object], guidance: str | None, level: DetailLevel,
) -> str:
    t = str(issue.get("title", ""))
    body = str(issue.get("body", ""))
    num, labels = issue.get("number", ""), issue.get("labels", [])
    lbl = ", ".join(str(x) for x in labels) if isinstance(labels, list) else str(labels)
    inst = {DetailLevel.L1: "One-liner summary only.",
            DetailLevel.L2: "Story list with dependency graph.",
            DetailLevel.L3: "Full detailed PRD."}[level]
    g = guidance or "No architecture guidance."
    return (
        f"You are a PRD generator for Dark Factory.\n\n## Task\n\n{inst}\n\n"
        f"## Issue\n\n**Title:** {t} (#{num})\n**Labels:** {lbl}\n\n{body}\n\n"
        f"## Arch Guidance\n\n{g}\n\n## Rules\n\n"
        f"1. Atomic stories (<=2 files). 2. Dependency order. 3. Max {_MAX_STORIES} stories.\n"
        "4. Testable acceptance criteria. 5. Priority: high/medium/low.\n\n"
        "## Output\n\nJSON only (no fences):\n\n"
        '{"title":"T","description":"D","user_stories":[{"id":"US-1","title":"S",'
        '"description":"As a...","acceptance_criteria":["c"],"priority":"high",'
        '"depends_on":[]}],"non_functional_requirements":["NFR"],"out_of_scope":["X"]}'
    )


def _parse_stories(raw: list[object]) -> tuple[UserStory, ...]:
    out: list[UserStory] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        out.append(UserStory(
            id=str(it.get("id", f"US-{len(out) + 1}")),
            title=str(it.get("title", "")),
            description=str(it.get("description", "")),
            acceptance_criteria=tup(it.get("acceptance_criteria", [])),
            priority=str(it.get("priority", "medium")),
            depends_on=tup(it.get("depends_on", [])),
        ))
    return tuple(out)


def _process(raw: str, level: DetailLevel, num: int | str,
             state_dir: Path | None) -> PRDResult:
    d = extract_json(raw)
    rs = d.get("user_stories", [])
    result = PRDResult(
        title=str(d.get("title", "")), description=str(d.get("description", "")),
        user_stories=_parse_stories(rs if isinstance(rs, list) else []),
        non_functional_requirements=tup(d.get("non_functional_requirements", [])),
        out_of_scope=tup(d.get("out_of_scope", [])),
        detail_level=level, raw_output=raw,
    )
    save_artifact(json.dumps({
        "title": result.title, "description": result.description,
        "detail_level": result.detail_level.value,
        "user_stories": [
            {"id": s.id, "title": s.title, "description": s.description,
             "acceptance_criteria": list(s.acceptance_criteria),
             "priority": s.priority, "depends_on": list(s.depends_on)}
            for s in result.user_stories],
        "non_functional_requirements": list(result.non_functional_requirements),
        "out_of_scope": list(result.out_of_scope),
    }, indent=2, ensure_ascii=False), "prd.json", num, state_dir=state_dir)
    return result


def _err(lv: DetailLevel, raw: str = "", e: str = "") -> PRDResult:
    return PRDResult(
        title="", description="", user_stories=(),
        non_functional_requirements=(), out_of_scope=(),
        detail_level=lv, raw_output=raw, errors=(e,) if e else (),
    )


def generate_prd(
    issue: dict[str, object], arch_guidance: str | None = None, *,
    detail_level: DetailLevel = DetailLevel.L3,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> PRDResult:
    """Generate a PRD from *issue* and optional *arch_guidance*."""
    rn = issue.get("number", 0)
    num = int(rn) if isinstance(rn, (int, float, str)) else 0
    return run_generate(
        "PRD", _build_prompt(issue, arch_guidance, detail_level),
        lambda raw: _process(raw, detail_level, num, state_dir),
        lambda raw, e: _err(detail_level, raw, e),
        invoke_fn=invoke_fn,
    )
