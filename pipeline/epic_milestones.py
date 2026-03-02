"""Epic grouping via GitHub Milestones, batch dispatch, and status summary."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from factory.integrations.shell import CommandError, gh

if TYPE_CHECKING:
    from factory.core.config_manager import ConfigData

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Milestone:
    """A GitHub Milestone representing an epic."""

    number: int
    title: str


@dataclass(frozen=True, slots=True)
class EpicStatus:
    """Completion status for a single epic (milestone)."""

    title: str
    open_issues: int
    closed_issues: int
    blocked_count: int = 0

    @property
    def total(self) -> int:
        return self.open_issues + self.closed_issues

    @property
    def pct(self) -> float:
        return (self.closed_issues / self.total * 100.0) if self.total > 0 else 0.0


@dataclass(frozen=True, slots=True)
class DispatchSummary:
    """Summary of a batch epic dispatch."""

    epic_title: str
    dispatched: int
    blocked: int
    total: int


def _get_repo(config: ConfigData) -> str:
    """Extract repo slug from config or environment."""
    project = config.data.get("project", {})
    if isinstance(project, dict):
        repo = project.get("repo", "")
        if isinstance(repo, str) and repo:
            return repo
    return os.environ.get("DARK_FACTORY_REPO", os.environ.get("REPO", ""))


def _extract_dep_numbers(body: str) -> list[int]:
    """Extract ``#NNN`` issue references from dependency sections."""
    section = re.search(
        r"## Blocked by.*?(?=^## |\Z)", body, re.MULTILINE | re.DOTALL,
    )
    text = section.group() if section else body
    return [int(m.group(1)) for m in re.finditer(r"#(\d+)", text)]


def _is_blocked(body: str, closed_numbers: set[int]) -> bool:
    """Return ``True`` if the issue has unresolved dependencies."""
    if not re.search(r"depends on|blocked by", body, re.IGNORECASE):
        return False
    return any(d not in closed_numbers for d in _extract_dep_numbers(body))


def get_or_create_milestone(repo: str, epic_title: str) -> Milestone:
    """Create or retrieve a GitHub Milestone (idempotent)."""
    if not epic_title:
        msg = "epic_title is required"
        raise ValueError(msg)
    result = gh(
        ["api", f"repos/{repo}/milestones", "--method", "GET", "--jq", "."],
        check=True,
    )
    milestones = json.loads(result.stdout) if result.stdout.strip() else []
    for ms in milestones:
        if isinstance(ms, dict) and ms.get("title") == epic_title:
            num = int(ms.get("number", 0))
            logger.info("Milestone exists: '%s' (#%d)", epic_title, num)
            return Milestone(number=num, title=epic_title)
    create_result = gh(
        ["api", f"repos/{repo}/milestones", "--method", "POST",
         "--field", f"title={epic_title}", "--jq", ".number"],
        check=True,
    )
    num = int(create_result.stdout.strip())
    logger.info("Created milestone: '%s' (#%d)", epic_title, num)
    return Milestone(number=num, title=epic_title)


def dispatch_epic(milestone: Milestone, config: ConfigData) -> DispatchSummary:
    """Dispatch all unblocked backlog issues in an epic.

    Swaps ``backlog`` -> ``arch-review`` for issues that have no
    unresolved dependencies.
    """
    repo = _get_repo(config)
    if not repo:
        msg = "No repo in config['project']['repo'] or DARK_FACTORY_REPO env"
        raise ValueError(msg)
    result = gh(
        ["issue", "list", "--repo", repo,
         "--label", "backlog", "--label", "factory-task",
         "--milestone", milestone.title, "--state", "open",
         "--json", "number,body", "--limit", "100"],
        check=True,
    )
    issues = json.loads(result.stdout) if result.stdout.strip() else []
    if not issues:
        return DispatchSummary(milestone.title, dispatched=0, blocked=0, total=0)
    closed_result = gh(
        ["issue", "list", "--repo", repo,
         "--label", "factory-task", "--state", "closed",
         "--json", "number", "--limit", "200"],
        check=True,
    )
    closed_raw = json.loads(closed_result.stdout) if closed_result.stdout.strip() else []
    closed_nums = {int(c["number"]) for c in closed_raw if isinstance(c, dict)}
    dispatched, blocked = 0, 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        num = issue.get("number", 0)
        body = str(issue.get("body", "") or "")
        if _is_blocked(body, closed_nums):
            logger.info("SKIP #%d (blocked)", num)
            blocked += 1
            continue
        try:
            gh(["issue", "edit", str(num), "--repo", repo,
                "--add-label", "arch-review", "--remove-label", "backlog"],
               check=True)
            dispatched += 1
            logger.info("DISPATCH #%d", num)
        except CommandError:
            logger.warning("Failed to dispatch #%d", num)
    return DispatchSummary(
        epic_title=milestone.title, dispatched=dispatched,
        blocked=blocked, total=len(issues),
    )


def epic_status_summary(repo: str) -> list[EpicStatus]:
    """Fetch all milestones and return epic status with completion counts."""
    try:
        result = gh(
            ["api", f"repos/{repo}/milestones", "--method", "GET", "--jq", "."],
            check=True,
        )
    except CommandError:
        logger.warning("Failed to fetch milestones for %s", repo)
        return []
    milestones = json.loads(result.stdout) if result.stdout.strip() else []
    statuses: list[EpicStatus] = []
    for ms in milestones:
        if not isinstance(ms, dict):
            continue
        title = ms.get("title", "")
        if not isinstance(title, str) or not title:
            continue
        statuses.append(EpicStatus(
            title=title,
            open_issues=int(ms.get("open_issues", 0)),
            closed_issues=int(ms.get("closed_issues", 0)),
        ))
    return statuses


def format_epic_summary(statuses: list[EpicStatus]) -> str:
    """Format epic statuses as a human-readable summary string."""
    if not statuses:
        return "No epics (milestones) found."
    lines: list[str] = ["Epic Status Summary", "=" * 40]
    g_open, g_closed = 0, 0
    for es in statuses:
        filled = int(es.pct / 100 * 20) if es.total > 0 else 0
        bar = "#" * filled + "-" * (20 - filled)
        lines.append(f"  {es.title:<30s} [{bar}] {es.closed_issues}/{es.total} ({es.pct:.0f}%)")
        g_open += es.open_issues
        g_closed += es.closed_issues
    g_total = g_open + g_closed
    pct = (g_closed / g_total * 100.0) if g_total > 0 else 0.0
    lines.extend(["", f"Overall: {g_closed}/{g_total} ({pct:.0f}%)"])
    return "\n".join(lines)
