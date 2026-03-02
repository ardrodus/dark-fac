"""PRD generator — convert GitHub issue + arch guidance into a PRD.

Ports generate-prd.sh.  Invokes Claude to produce a PRDResult with
user stories and acceptance criteria.  Supports L1/L2/L3 detail levels.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT = 300
_MAX_STORIES = 20
_STATE_DIR = Path(".dark-factory")


class DetailLevel(Enum):
    """PRD zoom level."""
    L1 = "L1"  # one-liner summary
    L2 = "L2"  # story list + dependency graph
    L3 = "L3"  # full detailed PRD


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


def _tup(raw: object) -> tuple[str, ...]:
    """Coerce list-or-scalar to tuple of strings."""
    if isinstance(raw, list):
        return tuple(str(x) for x in raw)
    return (str(raw),) if raw else ()


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


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _parse_stories(raw: list[object]) -> tuple[UserStory, ...]:
    out: list[UserStory] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        out.append(UserStory(
            id=str(it.get("id", f"US-{len(out) + 1}")),
            title=str(it.get("title", "")),
            description=str(it.get("description", "")),
            acceptance_criteria=_tup(it.get("acceptance_criteria", [])),
            priority=str(it.get("priority", "medium")),
            depends_on=_tup(it.get("depends_on", [])),
        ))
    return tuple(out)


def _parse_result(raw: str, level: DetailLevel) -> PRDResult:
    text = _strip_fences(raw)
    m = re.search(r"\{", text)
    if m:
        text = text[m.start():]
    d: dict[str, object] = json.loads(text)
    rs = d.get("user_stories", [])
    return PRDResult(
        title=str(d.get("title", "")), description=str(d.get("description", "")),
        user_stories=_parse_stories(rs if isinstance(rs, list) else []),
        non_functional_requirements=_tup(d.get("non_functional_requirements", [])),
        out_of_scope=_tup(d.get("out_of_scope", [])),
        detail_level=level, raw_output=raw,
    )


def _save_prd(
    result: PRDResult, num: int | str, *, state_dir: Path | None = None,
) -> Path:
    sd = (state_dir or _STATE_DIR) / "specs" / str(num)
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "prd.json"
    payload = {
        "title": result.title, "description": result.description,
        "detail_level": result.detail_level.value,
        "user_stories": [
            {"id": s.id, "title": s.title, "description": s.description,
             "acceptance_criteria": list(s.acceptance_criteria),
             "priority": s.priority, "depends_on": list(s.depends_on)}
            for s in result.user_stories],
        "non_functional_requirements": list(result.non_functional_requirements),
        "out_of_scope": list(result.out_of_scope),
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("PRD saved to %s", out)
    return out


def _err(lv: DetailLevel, raw: str = "", e: str = "") -> PRDResult:
    return PRDResult(
        title="", description="", user_stories=(),
        non_functional_requirements=(), out_of_scope=(),
        detail_level=lv, raw_output=raw, errors=(e,) if e else (),
    )


def generate_prd(
    issue: dict[str, object],
    arch_guidance: str | None = None,
    *,
    detail_level: DetailLevel = DetailLevel.L3,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> PRDResult:
    """Generate a PRD from *issue* and optional *arch_guidance*."""
    rn = issue.get("number", 0)
    num = int(rn) if isinstance(rn, (int, float, str)) else 0
    prompt = _build_prompt(issue, arch_guidance, detail_level)
    try:
        raw = _invoke_agent(prompt, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("PRD agent failed: %s", exc)
        return _err(detail_level, e=str(exc))
    try:
        result = _parse_result(raw, detail_level)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Failed to parse PRD: %s", exc)
        return _err(detail_level, raw, f"parse: {exc}")
    _save_prd(result, num, state_dir=state_dir)
    return result
