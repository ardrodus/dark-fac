"""PRD ingestion — parse a PRD, validate stories, split oversized, create Issues."""
from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)
_REQUIRED = ("id", "title", "description", "acceptance_criteria")
_AC_WARN = 10  # suggest split when AC items exceed this


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of a full ingest run."""
    created: int = 0
    skipped: int = 0
    failed: int = 0
    split: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def _read_json(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        stories = data.get("user_stories", [])
        return stories if isinstance(stories, list) else []
    return data if isinstance(data, list) else []


def _parse_md_story(block: str, idx: int) -> dict[str, object]:
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


def _read_md(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=##\s)", text)
    stories: list[dict[str, object]] = []
    for i, blk in enumerate(blocks, 1):
        blk = blk.strip()
        if not blk:
            continue
        if re.search(r"\bUS-\d+\b", blk) or re.search(r"(?i)story", blk):
            stories.append(_parse_md_story(blk, i))
    return stories


def _read_prd(path: Path) -> list[dict[str, object]]:
    return _read_md(path) if path.suffix.lower() in (".md", ".markdown") else _read_json(path)


def _validate(story: dict[str, object]) -> list[str]:
    return [f"missing: {f}" for f in _REQUIRED
            if story.get(f) is None or story.get(f) == "" or story.get(f) == []]


def _ac_len(story: dict[str, object]) -> int:
    ac = story.get("acceptance_criteria")
    return len(ac) if isinstance(ac, list) else 0


def _split_story(story: dict[str, object]) -> list[dict[str, object]]:
    ac = story.get("acceptance_criteria", [])
    if not isinstance(ac, list) or len(ac) <= _AC_WARN:
        return [story]
    sid, title = str(story.get("id", "?")), str(story.get("title", ""))
    desc, pri = str(story.get("description", "")), story.get("priority", "medium")
    subs: list[dict[str, object]] = []
    for ci, start in enumerate(range(0, len(ac), _AC_WARN), 1):
        subs.append({"id": f"{sid}-{ci}", "title": f"{title} (part {ci})",
                      "description": desc, "acceptance_criteria": ac[start:start + _AC_WARN],
                      "priority": pri})
    return subs


def _build_body(story: dict[str, object]) -> str:
    desc = str(story.get("description", ""))
    ac = story.get("acceptance_criteria", [])
    lines = [desc, "", "## Acceptance Criteria", ""]
    if isinstance(ac, list):
        lines.extend(f"- [ ] {item}" for item in ac)
    deps = story.get("depends_on", [])
    if isinstance(deps, list) and deps:
        lines.extend(["", "## Dependencies", ""])
        lines.extend(f"- {d}" for d in deps)
    return "\n".join(lines)


def _create_issue(
    story: dict[str, object], repo: str, *, gh_fn: object | None = None,
) -> int | None:
    title = f"[{story.get('id', '?')}] {story.get('title', 'Untitled')}"
    body = _build_body(story)
    pri = str(story.get("priority", "medium"))
    args = ["issue", "create", "--title", title, "--body", body,
            "--label", f"queued,priority:{pri}", "-R", repo]
    if gh_fn is not None:
        result = gh_fn(args, check=True)  # type: ignore[operator]
    else:
        from dark_factory.integrations.shell import gh  # noqa: PLC0415
        result = gh(args, check=True)
    m = re.search(r"/issues/(\d+)", result.stdout)
    return int(m.group(1)) if m else None


def ingest_prd(
    path: Path,
    repo: str,
    validate_only: bool = False,
    force: bool = False,
    auto_split: bool = False,
    *,
    gh_fn: object | None = None,
) -> IngestResult:
    """Ingest a PRD: read, validate, optionally split, create GitHub Issues."""
    if not path.exists():
        sys.stderr.write(f"Error: PRD not found: {path}\n")
        return IngestResult(errors=(f"File not found: {path}",))
    stories = _read_prd(path)
    if not stories:
        sys.stderr.write("Error: no stories found in PRD.\n")
        return IngestResult(errors=("No stories found",))
    valid: list[dict[str, object]] = []
    skip = 0
    for s in stories:
        errs = _validate(s)
        sid = str(s.get("id", "?"))
        if errs:
            sys.stdout.write(f"  [FAIL] {sid}: {'; '.join(errs)}\n")
            skip += 1
        else:
            n = _ac_len(s)
            if n > _AC_WARN:
                sys.stdout.write(f"  [WARN] {sid}: {n} AC items (suggest split)\n")
            sys.stdout.write(f"  [OK]   {sid}\n")
            valid.append(s)
    if validate_only:
        return IngestResult(skipped=skip)
    split_count = 0
    if auto_split:
        expanded: list[dict[str, object]] = []
        for s in valid:
            subs = _split_story(s)
            if len(subs) > 1:
                split_count += 1
                sys.stdout.write(f"  Split {s.get('id')}: {len(subs)} sub-stories\n")
            expanded.extend(subs)
        valid = expanded
    if not valid:
        return IngestResult(skipped=skip)
    if not force:
        sys.stdout.write(f"\nCreate {len(valid)} issue(s) in {repo}? [y/N] ")
        sys.stdout.flush()
        try:
            ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return IngestResult(skipped=skip + len(valid), split=split_count)
        if ans not in ("y", "yes"):
            return IngestResult(skipped=skip + len(valid), split=split_count)
    created, failed = 0, 0
    err_out: list[str] = []
    for s in valid:
        sid = str(s.get("id", "?"))
        try:
            num = _create_issue(s, repo, gh_fn=gh_fn)
            sys.stdout.write(f"  Created #{num or '?'}: {sid}\n")
            created += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Issue creation failed for %s: %s", sid, exc)
            err_out.append(f"{sid}: {exc}")
            failed += 1
    sys.stdout.write(f"\nDone: {created} created, {skip} skipped, "
                     f"{failed} failed, {split_count} split\n")
    return IngestResult(created=created, skipped=skip, failed=failed,
                        split=split_count, errors=tuple(err_out))
