"""Safe, typed wrapper for GitHub CLI issue operations.

Provides high-level helpers for listing, labelling, and fetching issues
via the ``gh`` convenience wrapper in :mod:`factory.integrations.shell`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from factory.integrations.shell import CommandError, gh

logger = logging.getLogger(__name__)


class GhSafeError(Exception):
    """Raised when a GitHub CLI operation fails."""


@dataclass(frozen=True, slots=True)
class IssueInfo:
    """Minimal representation of a GitHub issue."""

    number: int
    title: str
    labels: tuple[str, ...]
    state: str


def list_issues(
    *,
    labels: list[str] | None = None,
    state: str = "open",
    limit: int = 30,
    repo: str | None = None,
    cwd: str | None = None,
) -> list[IssueInfo]:
    """List issues matching the given filters."""
    args = ["issue", "list", "--json", "number,title,labels,state", "--state", state, "--limit", str(limit)]
    if labels:
        args.extend(["--label", ",".join(labels)])
    if repo:
        args.extend(["--repo", repo])
    try:
        result = gh(args, check=True, cwd=cwd)
    except CommandError as exc:
        raise GhSafeError(f"Failed to list issues: {exc}") from exc

    try:
        raw: list[dict[str, object]] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GhSafeError(f"Invalid JSON from gh issue list: {exc}") from exc

    issues: list[IssueInfo] = []
    for item in raw:
        label_names: list[str] = []
        raw_labels = item.get("labels", [])
        if isinstance(raw_labels, list):
            for lbl in raw_labels:
                if isinstance(lbl, dict):
                    name = lbl.get("name", "")
                    if isinstance(name, str):
                        label_names.append(name)
                elif isinstance(lbl, str):
                    label_names.append(lbl)
        number = item.get("number", 0)
        title = item.get("title", "")
        issue_state = item.get("state", "OPEN")
        issues.append(
            IssueInfo(
                number=int(number) if isinstance(number, (int, float)) else 0,
                title=str(title),
                labels=tuple(label_names),
                state=str(issue_state),
            )
        )
    return issues


def add_label(issue_number: int, label: str, *, repo: str | None = None, cwd: str | None = None) -> None:
    """Add a label to an issue."""
    args = ["issue", "edit", str(issue_number), "--add-label", label]
    if repo:
        args.extend(["--repo", repo])
    try:
        gh(args, check=True, cwd=cwd)
    except CommandError as exc:
        raise GhSafeError(f"Failed to add label '{label}' to #{issue_number}: {exc}") from exc


def remove_label(issue_number: int, label: str, *, repo: str | None = None, cwd: str | None = None) -> None:
    """Remove a label from an issue."""
    args = ["issue", "edit", str(issue_number), "--remove-label", label]
    if repo:
        args.extend(["--repo", repo])
    try:
        gh(args, check=True, cwd=cwd)
    except CommandError as exc:
        raise GhSafeError(f"Failed to remove label '{label}' from #{issue_number}: {exc}") from exc
