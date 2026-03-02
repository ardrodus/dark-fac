"""PRD ingestion — read a PRD file, validate stories, and create GitHub Issues.

Supports JSON and Markdown PRD formats.  Validates that each story has
required fields (id, title, description, acceptance_criteria) before
creating issues.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("id", "title", "description", "acceptance_criteria")
_LABEL = "dark-factory"


@dataclass(frozen=True, slots=True)
class StoryValidation:
    """Validation result for a single story."""
    story_id: str
    errors: tuple[str, ...]
    valid: bool


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Result of a full ingest run."""
    passed: bool
    stories_total: int
    stories_valid: int
    issues_created: int
    issues_skipped: int
    validations: tuple[StoryValidation, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


# ── PRD Reading ──────────────────────────────────────────────────


def _read_json_prd(path: Path) -> list[dict[str, object]]:
    """Parse a JSON PRD file and return the user_stories list."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        stories = data.get("user_stories", [])
        return stories if isinstance(stories, list) else []
    if isinstance(data, list):
        return data
    return []


def _parse_md_story(block: str, idx: int) -> dict[str, object]:
    """Extract story fields from a Markdown heading block."""
    story: dict[str, object] = {}
    id_m = re.search(r"\b(US-\d+)\b", block)
    story["id"] = id_m.group(1) if id_m else f"US-{idx}"
    title_m = re.match(r"#+\s*(?:US-\d+[:\s\-]*)?(.+)", block)
    story["title"] = title_m.group(1).strip() if title_m else ""
    ac: list[str] = []
    in_ac = False
    desc_lines: list[str] = []
    for line in block.split("\n")[1:]:
        stripped = line.strip()
        if re.match(r"(?i)^(?:#{1,4}\s*)?acceptance.criteria", stripped):
            in_ac = True
            continue
        if in_ac:
            bullet = re.match(r"^[-*]\s+(.*)", stripped)
            if bullet:
                ac.append(bullet.group(1))
        elif stripped:
            desc_lines.append(stripped)
    story["description"] = " ".join(desc_lines) if desc_lines else ""
    story["acceptance_criteria"] = ac
    return story


def _read_md_prd(path: Path) -> list[dict[str, object]]:
    """Parse a Markdown PRD file and return extracted stories."""
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=##\s)", text)
    stories: list[dict[str, object]] = []
    for i, block in enumerate(blocks, 1):
        block = block.strip()
        if not block:
            continue
        if re.search(r"\bUS-\d+\b", block) or re.search(r"(?i)story", block):
            stories.append(_parse_md_story(block, i))
    return stories


def _read_prd(path: Path) -> list[dict[str, object]]:
    """Read a PRD file (JSON or Markdown) and return story dicts."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _read_json_prd(path)
    if suffix in (".md", ".markdown"):
        return _read_md_prd(path)
    return _read_json_prd(path)


# ── Validation ───────────────────────────────────────────────────


def _validate_story(story: dict[str, object]) -> StoryValidation:
    """Validate that a single story has the required fields."""
    sid = str(story.get("id", "?"))
    errors: list[str] = []
    for fld in _REQUIRED_FIELDS:
        val = story.get(fld)
        if val is None or val == "" or val == []:
            errors.append(f"missing or empty field: {fld}")
    ac = story.get("acceptance_criteria")
    if isinstance(ac, list) and len(ac) == 0:
        pass  # already caught above
    elif isinstance(ac, list):
        for i, item in enumerate(ac):
            if not isinstance(item, str) or not item.strip():
                errors.append(f"acceptance_criteria[{i}] is empty")
    return StoryValidation(story_id=sid, errors=tuple(errors), valid=len(errors) == 0)


def _validate_all(stories: list[dict[str, object]]) -> tuple[StoryValidation, ...]:
    """Validate all stories and return validation results."""
    return tuple(_validate_story(s) for s in stories)


# ── Issue Creation ───────────────────────────────────────────────


def _build_issue_body(story: dict[str, object]) -> str:
    """Build the GitHub Issue body from a story dict."""
    desc = str(story.get("description", ""))
    ac = story.get("acceptance_criteria", [])
    lines = [desc, "", "## Acceptance Criteria", ""]
    if isinstance(ac, list):
        for item in ac:
            lines.append(f"- [ ] {item}")
    deps = story.get("depends_on", [])
    if isinstance(deps, list) and deps:
        lines.extend(["", "## Dependencies", ""])
        for dep in deps:
            lines.append(f"- {dep}")
    return "\n".join(lines)


def _create_issue(
    story: dict[str, object],
    *,
    gh_fn: object | None = None,
) -> int | None:
    """Create a GitHub Issue for a story. Returns issue number or None."""
    title = f"[{story.get('id', '?')}] {story.get('title', 'Untitled')}"
    body = _build_issue_body(story)
    priority = str(story.get("priority", "medium"))
    labels = [_LABEL, f"priority:{priority}"]
    args = [
        "issue", "create",
        "--title", title,
        "--body", body,
        "--label", ",".join(labels),
    ]
    if gh_fn is not None:
        result = gh_fn(args, check=True)  # type: ignore[operator]
    else:
        from factory.integrations.shell import gh  # noqa: PLC0415
        result = gh(args, check=True)
    m = re.search(r"/issues/(\d+)", result.stdout)
    return int(m.group(1)) if m else None


# ── Orchestration ────────────────────────────────────────────────


def _confirm(story_count: int) -> bool:
    """Ask for user confirmation before creating issues."""
    sys.stdout.write(f"\nAbout to create {story_count} GitHub issue(s). Continue? [y/N] ")
    sys.stdout.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def ingest_prd(
    *,
    prd_path: str,
    validate_only: bool = False,
    force: bool = False,
    gh_fn: object | None = None,
) -> IngestResult:
    """Ingest a PRD file: read, validate, and optionally create GitHub Issues."""
    path = Path(prd_path)
    if not path.exists():
        sys.stderr.write(f"Error: PRD file not found: {prd_path}\n")
        return IngestResult(passed=False, stories_total=0, stories_valid=0,
                            issues_created=0, issues_skipped=0,
                            errors=(f"File not found: {prd_path}",))

    stories = _read_prd(path)
    if not stories:
        sys.stderr.write("Error: No stories found in PRD file.\n")
        return IngestResult(passed=False, stories_total=0, stories_valid=0,
                            issues_created=0, issues_skipped=0,
                            errors=("No stories found",))

    validations = _validate_all(stories)
    valid_count = sum(1 for v in validations if v.valid)
    invalid_count = len(validations) - valid_count

    sys.stdout.write(f"\nPRD: {path.name} ({len(stories)} stories)\n")
    for v in validations:
        if v.valid:
            sys.stdout.write(f"  [OK]   {v.story_id}\n")
        else:
            sys.stdout.write(f"  [FAIL] {v.story_id}: {'; '.join(v.errors)}\n")

    if invalid_count:
        sys.stdout.write(f"\nValidation: {valid_count} valid, {invalid_count} invalid\n")

    if validate_only:
        passed = invalid_count == 0
        sys.stdout.write(f"\nValidation {'PASSED' if passed else 'FAILED'}\n")
        return IngestResult(passed=passed, stories_total=len(stories),
                            stories_valid=valid_count, issues_created=0,
                            issues_skipped=invalid_count, validations=validations)

    valid_stories = [s for s, v in zip(stories, validations, strict=False) if v.valid]
    if not valid_stories:
        sys.stderr.write("Error: No valid stories to create issues for.\n")
        return IngestResult(passed=False, stories_total=len(stories),
                            stories_valid=0, issues_created=0,
                            issues_skipped=len(stories), validations=validations)

    if not force and not _confirm(len(valid_stories)):
        sys.stdout.write("Aborted.\n")
        return IngestResult(passed=True, stories_total=len(stories),
                            stories_valid=valid_count, issues_created=0,
                            issues_skipped=len(stories), validations=validations)

    created = 0
    skipped = 0
    errors: list[str] = []
    for story in valid_stories:
        sid = str(story.get("id", "?"))
        try:
            num = _create_issue(story, gh_fn=gh_fn)
            if num is not None:
                sys.stdout.write(f"  Created #{num}: {sid}\n")
                created += 1
            else:
                sys.stdout.write(f"  Created (unknown #): {sid}\n")
                created += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to create issue for %s: %s", sid, exc)
            sys.stdout.write(f"  Skipped {sid}: {exc}\n")
            errors.append(f"{sid}: {exc}")
            skipped += 1

    sys.stdout.write(f"\nSummary: {created} created, {skipped + invalid_count} skipped\n")
    return IngestResult(
        passed=len(errors) == 0,
        stories_total=len(stories),
        stories_valid=valid_count,
        issues_created=created,
        issues_skipped=skipped + invalid_count,
        validations=validations,
        errors=tuple(errors),
    )
