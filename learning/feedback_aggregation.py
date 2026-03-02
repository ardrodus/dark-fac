"""Feedback aggregation — extract PR review feedback, detect patterns, apply fixes, generate digest."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_WIDESPREAD_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class FeedbackInstance:
    """A single piece of feedback extracted from a PR review comment."""

    body: str
    author: str = ""
    path: str = ""
    pattern: str = ""
    created_at: str = ""


# ── GitHub helpers ──────────────────────────────────────────────


def _gh(*args: str) -> str:
    """Run a ``gh`` CLI command, returning stdout or empty on failure."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("gh %s failed: %s", args[0], result.stderr.strip())
            return ""
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("gh CLI unavailable or timed out: %s", exc)
        return ""


# ── Pattern classification ──────────────────────────────────────


_PATTERN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("naming", re.compile(r"renam|naming|name.*(should|convention)", re.I)),
    ("error-handling", re.compile(r"error.handl|exception|try.catch|raise", re.I)),
    ("type-safety", re.compile(r"type.*(hint|annot|safe)|mypy|typing", re.I)),
    ("testing", re.compile(r"test|assert|coverage|mock|fixture", re.I)),
    ("docs", re.compile(r"docstring|comment|document|readme", re.I)),
    ("style", re.compile(r"format|indent|whitespace|lint|ruff|style", re.I)),
    ("security", re.compile(r"secur|inject|sanitiz|escap|vulnerab", re.I)),
    ("performance", re.compile(r"perf|optim|slow|cache|O\(n", re.I)),
)


def _classify_pattern(text: str) -> str:
    """Return the best-matching pattern tag for *text*, or ``'general'``."""
    for tag, pat in _PATTERN_RULES:
        if pat.search(text):
            return tag
    return "general"


# ── Core API ────────────────────────────────────────────────────


def extract_feedback(repo: str, pr_number: int) -> list[FeedbackInstance]:
    """Parse PR review comments and return structured feedback instances."""
    if not repo or pr_number < 1:
        logger.error("extract_feedback: invalid repo=%r or pr_number=%d", repo, pr_number)
        return []
    raw = _gh("pr", "view", str(pr_number), "--repo", repo, "--json", "reviews,comments")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("extract_feedback: invalid JSON from gh CLI")
        return []
    instances: list[FeedbackInstance] = []
    for review in data.get("reviews", []):
        body = (review.get("body") or "").strip()
        if not body:
            continue
        instances.append(FeedbackInstance(
            body=body, author=review.get("author", {}).get("login", ""),
            pattern=_classify_pattern(body), created_at=review.get("submittedAt", ""),
        ))
    for comment in data.get("comments", []):
        body = (comment.get("body") or "").strip()
        if not body:
            continue
        instances.append(FeedbackInstance(
            body=body, author=comment.get("author", {}).get("login", ""),
            path=comment.get("path", ""), pattern=_classify_pattern(body),
            created_at=comment.get("createdAt", ""),
        ))

    logger.info("Extracted %d feedback instances from %s#%d", len(instances), repo, pr_number)
    return instances


def is_widespread(pattern: str, instances: list[FeedbackInstance]) -> bool:
    """Return *True* if *pattern* appears in >= threshold distinct instances."""
    count = sum(1 for fb in instances if fb.pattern == pattern)
    return count >= _WIDESPREAD_THRESHOLD


def apply_widespread_fix(pattern: str, workspace: Workspace) -> None:
    """Record *pattern* in the knowledge system (US-805) with boosted confidence."""
    from factory.knowledge.patterns import Pattern, PatternStore

    store = PatternStore(workspace.path)
    existing = store.get(pattern)
    if existing is not None:
        store.update_confidence(pattern, success=True)
        logger.info("Boosted confidence for existing pattern %r", pattern)
    else:
        store.add(Pattern(
            name=pattern,
            type=pattern,
            content=f"Widespread feedback pattern detected in PR reviews: {pattern}",
            confidence=0.7,
            tags=["feedback", "widespread"],
            source_repo=workspace.repo_url,
        ))
        logger.info("Recorded new widespread pattern %r", pattern)


def generate_digest(instances: list[FeedbackInstance]) -> str:
    """Generate a markdown summary of feedback instances for the learning system."""
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# PR Feedback Digest",
        "",
        f"> Generated: {now} | Instances: {len(instances)}",
        "",
    ]

    if not instances:
        lines.append("No feedback instances to report.")
        return "\n".join(lines)

    pattern_counts = Counter(fb.pattern for fb in instances)
    widespread = [p for p, c in pattern_counts.items() if c >= _WIDESPREAD_THRESHOLD]

    lines += [
        "## Summary",
        "",
        "| Pattern | Count | Widespread |",
        "|---------|-------|------------|",
    ]
    for pat, count in pattern_counts.most_common():
        mark = "Yes" if pat in widespread else "No"
        lines.append(f"| {pat} | {count} | {mark} |")

    lines += ["", "## Recent Feedback", ""]
    for i, fb in enumerate(instances[:20], 1):
        author = f" ({fb.author})" if fb.author else ""
        path = f" `{fb.path}`" if fb.path else ""
        preview = fb.body[:120].replace("\n", " ")
        if len(fb.body) > 120:
            preview += "..."
        lines.append(f"{i}. **[{fb.pattern}]**{author}{path}: {preview}")

    if widespread:
        lines += [
            "",
            "## Widespread Patterns",
            "",
            f"The following patterns appeared {_WIDESPREAD_THRESHOLD}+ times "
            "and may warrant codebase-wide fixes:",
            "",
        ]
        for pat in widespread:
            lines.append(f"- **{pat}** ({pattern_counts[pat]} instances)")

    lines += ["", "---", "*Generated by feedback_aggregation.py (US-819)*", ""]
    return "\n".join(lines)
