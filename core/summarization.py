"""L1/L2/L3 summarization engine for architecture guidance, PRDs, and memory.

Port of ``arch-guidance-levels.sh``, ``prd-levels.sh``, ``memory-levels.sh``.
L1 = 1-2 sentence executive summary, L2 = paragraph with key points,
L3 = detailed summary preserving all important information.
"""

from __future__ import annotations

import re
from typing import Literal

ContentType = Literal["arch_guidance", "prd", "memory", "design"]
_VALID_LEVELS = (1, 2, 3)

# Word limits per content type: (L1_max, L2_max)
_WORD_LIMITS: dict[ContentType, tuple[int, int]] = {
    "arch_guidance": (25, 120),
    "prd": (30, 150),
    "memory": (20, 100),
    "design": (25, 120),
}

_NA_PATTERNS = re.compile(
    r"\b(N/A|not applicable|no recommendations)\b", re.IGNORECASE,
)


def summarize(content: str, level: int, content_type: str) -> str:
    """Generate an L1, L2, or L3 summary of *content*.

    Raises ``ValueError`` if *level* is not 1, 2, or 3.
    """
    if level not in _VALID_LEVELS:
        raise ValueError(f"level must be 1, 2, or 3; got {level}")

    ct = _validate_content_type(content_type)

    if not content or not content.strip():
        return _empty_fallback(ct, level)

    if level == 3:
        return content.strip()
    if level == 2:
        return _generate_l2(content, ct)
    return _generate_l1(content, ct)


# ── Internal generators ───────────────────────────────────────────

def _generate_l1(content: str, content_type: ContentType) -> str:
    """Extract a 1-2 sentence executive summary (L1)."""
    l1_max, _ = _WORD_LIMITS[content_type]

    if content_type == "prd":
        return _prd_l1(content, l1_max)

    if content_type == "arch_guidance" and _is_na(content):
        return "N/A — domain not relevant to this stage."

    lines = _content_lines(content, include_headings=False)
    if not lines:
        return _empty_fallback(content_type, 1)

    first = _strip_md_bold(lines[0])
    return _truncate(first, l1_max)


def _generate_l2(content: str, content_type: ContentType) -> str:
    """Extract a paragraph-level summary with key points (L2)."""
    _, l2_max = _WORD_LIMITS[content_type]

    if content_type == "prd":
        return _prd_l2(content, l2_max)

    if content_type == "arch_guidance" and _is_na(content):
        return "N/A — domain not relevant to this stage."

    lines = _content_lines(content, include_headings=True)
    if not lines:
        return _empty_fallback(content_type, 2)

    collected: list[str] = []
    word_count = 0
    for line in lines:
        words = line.split()
        if word_count + len(words) > l2_max:
            break
        collected.append(line)
        word_count += len(words)

    if not collected:
        return _truncate(lines[0], l2_max)
    return _truncate(" ".join(collected), l2_max)


# ── PRD-specific extractors ───────────────────────────────────────

def _prd_l1(content: str, max_words: int) -> str:
    """L1 for PRDs: title + first Overview sentence."""
    title = ""
    title_match = re.search(r"^#\s+PRD:\s*(.+)", content, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()
    else:
        heading_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        if heading_match:
            title = heading_match.group(1).strip()

    overview_sent = ""
    ov_match = re.search(
        r"^##\s+Overview\s*\n+(.*?)(?:\n\n|\n##|\Z)",
        content, re.MULTILINE | re.DOTALL,
    )
    if ov_match:
        first_para = ov_match.group(1).strip()
        sentences = re.split(r"(?<=[.!?])\s+", first_para)
        if sentences:
            overview_sent = sentences[0]

    parts = [p for p in (title, overview_sent) if p]
    result = " -- ".join(parts) if parts else content.split("\n", 1)[0]
    return _truncate(result, max_words)


def _prd_l2(content: str, max_words: int) -> str:
    """L2 for PRDs: overview paragraph + story list."""
    sections: list[str] = []

    ov_match = re.search(
        r"^##\s+Overview\s*\n+(.*?)(?:\n##|\Z)",
        content, re.MULTILINE | re.DOTALL,
    )
    if ov_match:
        paragraphs = ov_match.group(1).strip().split("\n\n")
        if paragraphs:
            sections.append(paragraphs[0].strip())

    stories = re.findall(r"^###\s+(US-\d+):\s*(.+)", content, re.MULTILINE)
    if stories:
        sections.append("Stories: " + "; ".join(
            f"{sid} ({title.strip()})" for sid, title in stories
        ))

    result = " ".join(sections) if sections else content[:500]
    return _truncate(result, max_words)


# ── Helpers ───────────────────────────────────────────────────────

def _content_lines(text: str, *, include_headings: bool = False) -> list[str]:
    """Return non-empty, non-separator content lines from *text*."""
    result: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"[-=_*]{3,}", line):
            continue
        if line.startswith("#"):
            if include_headings:
                result.append(re.sub(r"^#+\s*", "", line))
            continue
        result.append(line)
    return result


def _strip_md_bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def _truncate(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


def _is_na(content: str) -> bool:
    """Detect N/A / not-applicable content (short text with NA markers)."""
    stripped = content.strip()
    return len(stripped.split()) < 30 and bool(_NA_PATTERNS.search(stripped))


def _empty_fallback(content_type: ContentType, level: int) -> str:
    return f"No {content_type.replace('_', ' ')} content available (L{level})."


def _validate_content_type(raw: str) -> ContentType:
    valid: set[str] = {"arch_guidance", "prd", "memory", "design"}
    if raw not in valid:
        raise ValueError(
            f"content_type must be one of {sorted(valid)}; got {raw!r}"
        )
    return raw  # type: ignore[return-value]
