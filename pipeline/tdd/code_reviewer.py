"""TDD Code Reviewer — review implementation diff against specs and standards.

Ports Stage 3 of tdd-pipeline.sh.  REQUEST_CHANGES triggers a feedback loop
back to the Feature Writer (max 3 rounds).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.workspace.manager import Workspace

from dark_factory.pipeline.tdd.test_writer import SpecBundle

logger = logging.getLogger(__name__)

_AGENT_TIMEOUT = 300
_MAX_REVIEW_ROUNDS = 3


class ReviewVerdict:
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"


@dataclass(frozen=True, slots=True)
class ReviewComment:
    """Single review comment attached to a file location."""
    file: str = ""
    line: int = 0
    message: str = ""


@dataclass(frozen=True, slots=True)
class CodeReviewResult:
    """Outcome of a Code Reviewer invocation."""
    verdict: str = ReviewVerdict.REQUEST_CHANGES
    comments: tuple[ReviewComment, ...] = ()
    blocking_issues: tuple[str, ...] = ()
    round_number: int = 1
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _build_prompt(
    specs: SpecBundle, workspace_path: str, diff: str,
    test_results: str = "", previous_feedback: str = "",
    round_number: int = 1, max_rounds: int = _MAX_REVIEW_ROUNDS,
) -> str:
    """Assemble reviewer prompt from specs, diff, and context."""
    parts: list[str] = [
        "You are the Code Reviewer agent in a TDD pipeline.",
        "Review the implementation diff against specs, tests, and quality standards.",
        "You see the FULL diff and ALL project artifacts.\n",
    ]
    for attr, heading in [
        ("prd", "## PRD / User Story"), ("design_doc", "## Design Document"),
        ("api_contract", "## API Contract"), ("schema_spec", "## Schema Spec"),
        ("interface_definitions", "## Interface Definitions"),
    ]:
        content = getattr(specs, attr)
        if content:
            parts.append(f"{heading}\n\n{content}\n")
    parts.append(f"## Implementation Diff\n\n```diff\n{diff}\n```\n")
    if test_results:
        parts.append(f"## Test Results\n\n{test_results}\n")
    if previous_feedback:
        parts.append(f"## Previous Review Feedback\n\n{previous_feedback}\n")
    parts.extend([
        f"Review round {round_number} of {max_rounds}.\n",
        "## Review Criteria\n",
        "- Correctness: implements the spec correctly?",
        "- Test alignment: test results confirm it works?",
        "- Code quality: clean, maintainable, follows conventions?",
        "- Security: no vulnerabilities introduced?",
        "- Minimal change: only what is necessary?\n",
        "## Output Format\n",
        "Output a JSON object:",
        '{"verdict": "APPROVE" or "REQUEST_CHANGES",',
        ' "comments": [{"file": "...", "line": 0, "message": "..."}],',
        ' "blocking_issues": ["issue description", ...]}',
        f"\nWorkspace root: {workspace_path}",
    ])
    return "\n".join(parts)


def _invoke_agent(
    prompt: str, workspace_path: str, *,
    invoke_fn: Callable[[str], str] | None = None,
) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from dark_factory.integrations.shell import run_command  # noqa: PLC0415

    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, check=True, cwd=workspace_path,
    ).stdout


def _parse_result(raw: str) -> tuple[str, list[ReviewComment], list[str]]:
    """Extract verdict, comments, and blocking issues from agent JSON."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", text, re.DOTALL) or \
        re.search(r"\{.*\"verdict\".*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    data: dict[str, object] = json.loads(text)
    raw_verdict = str(data.get("verdict", "REQUEST_CHANGES")).upper()
    verdict = (
        ReviewVerdict.APPROVE if "APPROV" in raw_verdict
        else ReviewVerdict.REQUEST_CHANGES
    )
    comments: list[ReviewComment] = []
    raw_comments = data.get("comments", [])
    if isinstance(raw_comments, list):
        for c in raw_comments:
            if isinstance(c, dict):
                comments.append(ReviewComment(
                    file=str(c.get("file", "")),
                    line=int(c.get("line", 0)),
                    message=str(c.get("message", "")),
                ))
    raw_blocking = data.get("blocking_issues", [])
    blocking = [str(b) for b in raw_blocking] if isinstance(raw_blocking, list) else []
    return verdict, comments, blocking


def run_code_reviewer(
    specs: SpecBundle,
    workspace: Workspace,
    diff: str,
    *,
    test_results: str = "",
    invoke_fn: Callable[[str], str] | None = None,
    fix_fn: Callable[[str], str] | None = None,
    max_rounds: int = _MAX_REVIEW_ROUNDS,
) -> CodeReviewResult:
    """Review implementation diff against specs and standards.

    When *fix_fn* is provided, REQUEST_CHANGES triggers a feedback loop:
    *fix_fn(feedback)* returns an updated diff for re-review (max 3 rounds).
    """
    ws_path = workspace.path
    previous_feedback = ""
    result = CodeReviewResult()
    for round_num in range(1, max_rounds + 1):
        prompt = _build_prompt(
            specs, ws_path, diff, test_results=test_results,
            previous_feedback=previous_feedback,
            round_number=round_num, max_rounds=max_rounds,
        )
        errors: list[str] = []
        try:
            raw = _invoke_agent(prompt, ws_path, invoke_fn=invoke_fn)
        except Exception as exc:  # noqa: BLE001
            logger.error("Code Reviewer round %d failed: %s", round_num, exc)
            return CodeReviewResult(
                verdict=ReviewVerdict.REQUEST_CHANGES,
                round_number=round_num, raw_output="", errors=(str(exc),),
            )
        try:
            verdict, comments, blocking = _parse_result(raw)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Parse error round %d: %s", round_num, exc)
            verdict, comments, blocking = ReviewVerdict.REQUEST_CHANGES, [], []
            errors.append(f"parse error: {exc}")
        result = CodeReviewResult(
            verdict=verdict, comments=tuple(comments),
            blocking_issues=tuple(blocking), round_number=round_num,
            raw_output=raw, errors=tuple(errors),
        )
        if verdict == ReviewVerdict.APPROVE:
            logger.info("Code review APPROVED at round %d", round_num)
            return result
        if fix_fn is None or round_num >= max_rounds:
            return result
        previous_feedback = raw
        logger.info("Sending feedback to Feature Writer (round %d)", round_num)
        diff = fix_fn(raw)
    return result
