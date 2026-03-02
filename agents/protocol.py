"""Agent Protocol — prompt assembly for every agent invocation.

Ports agent-protocol.sh: preamble/epilogue generation, role-based context
profiles (L1/L2/L3), cross-project pattern search, degraded mode fallbacks.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory.core.config_manager import ConfigData

logger = logging.getLogger(__name__)


class ZoomLevel(Enum):
    """Context detail level: tagline, paragraph, or full."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


_LEVEL_DESC = {
    ZoomLevel.L1: "tagline only (<20 words)",
    ZoomLevel.L2: "paragraph summary (<100 words)",
    ZoomLevel.L3: "full detail",
}


@dataclass(frozen=True, slots=True)
class ContextProfile:
    """Per-agent zoom levels for each context category."""

    own_domain: ZoomLevel = ZoomLevel.L3
    other_domains: ZoomLevel = ZoomLevel.L3
    task: ZoomLevel = ZoomLevel.L3
    history: ZoomLevel = ZoomLevel.L3


_DEFAULT = ContextProfile()

_PROFILES: dict[str, ContextProfile] = {
    "sa-specialist": ContextProfile(
        ZoomLevel.L3, ZoomLevel.L2, ZoomLevel.L3, ZoomLevel.L1),
    "sa-lead": ContextProfile(
        ZoomLevel.L2, ZoomLevel.L2, ZoomLevel.L3, ZoomLevel.L2),
    "test-writer": ContextProfile(
        ZoomLevel.L3, ZoomLevel.L2, ZoomLevel.L3, ZoomLevel.L1),
    "feature-writer": ContextProfile(
        ZoomLevel.L3, ZoomLevel.L1, ZoomLevel.L3, ZoomLevel.L1),
    "crucible": ContextProfile(
        ZoomLevel.L3, ZoomLevel.L2, ZoomLevel.L3, ZoomLevel.L1),
    "learning": ContextProfile(
        ZoomLevel.L3, ZoomLevel.L1, ZoomLevel.L1, ZoomLevel.L1),
}

_ALIASES: dict[str, str] = {
    "sa-compute": "sa-specialist", "sa-storage": "sa-specialist",
    "sa-database": "sa-specialist", "sa-network": "sa-specialist",
    "sa-analytics": "sa-specialist", "sa-mlai": "sa-specialist",
    "sa-devops": "sa-specialist", "sa-monitoring": "sa-specialist",
    "sa-integration": "sa-specialist", "sa-security": "sa-specialist",
    "sa-code-quality": "sa-specialist", "sa-performance": "sa-specialist",
    "sa-testing": "sa-specialist", "sa-dependencies": "sa-specialist",
    "sa-api-design": "sa-specialist", "sa-ux": "sa-specialist",
    "eng-test-writer": "test-writer", "eng-feature-writer": "feature-writer",
    "eng-crucible": "crucible", "code-review": "crucible",
}

_LEARNING_RE = re.compile(r"^learning(?:-.+)?$")


def get_context_profile(agent_type: str) -> ContextProfile:
    """Return the L1/L2/L3 context profile for *agent_type*."""
    if not agent_type:
        return _DEFAULT
    if agent_type in _PROFILES:
        return _PROFILES[agent_type]
    if agent_type in _ALIASES:
        return _PROFILES[_ALIASES[agent_type]]
    if _LEARNING_RE.match(agent_type):
        return _PROFILES["learning"]
    return _DEFAULT


def _project_key(config: ConfigData | None) -> str:
    """Derive claude-mem project key from config or env."""
    repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPO", "")
    if not repo and config is not None:
        repo = config.data.get("repo", "")
        if isinstance(repo, dict):
            repo = repo.get("name", "")
        if not isinstance(repo, str):
            repo = ""
    if repo:
        base = repo.rsplit("/", 1)[-1].lower()
        return re.sub(r"[^a-z0-9-]", "", base) or "dark-factory"
    return "dark-factory"


_XP_CAP = 5


def _shared_keys(current: str) -> list[str]:
    """Return claude-mem keys for repos with share_patterns: true (US-052)."""
    raw = os.environ.get("SHARE_PATTERNS_REPOS", "")
    if not raw:
        return []
    keys: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        key = re.sub(r"[^a-z0-9-]", "", part.rsplit("/", 1)[-1].lower())
        if key and key != current:
            keys.append(key)
    return keys


def _cross_project_section(task_desc: str, current: str) -> str:
    """Generate cross-project transferable pattern search section (US-048)."""
    keys = _shared_keys(current)
    if not keys:
        return ""
    searches = "\n".join(
        f'- Search for "[transferable] {task_desc}" with project="{k}"'
        for k in keys
    )
    return (
        "\n### 5. Cross-Project Transferable Patterns (US-048)\n\n"
        "Search for transferable patterns from other repos.\n\n"
        f"**Search each project**:\n{searches}\n\n"
        f"**Ranking**: relevance -> confidence -> recency.\n"
        f"**Cap**: At most **{_XP_CAP} cross-project patterns**.\n"
        "**Degradation**: If search fails, fall back to project-only.\n"
    )


def generate_preamble(agent_type: str, task_context: dict[str, Any] | None = None,
                      config: ConfigData | None = None) -> str:
    """Generate pre-work context loading instructions."""
    profile = get_context_profile(agent_type)
    proj = _project_key(config)
    label = agent_type or "default"
    td = (task_context or {}).get("task_description", "current task")
    lines = [
        "## Agent Protocol -- Pre-Work Context Loading\n",
        "Before starting, search claude-mem for existing knowledge. Run FIRST.\n",
        f"**Context Profile: {label}** -- each search specifies a zoom level.",
        'Append level tag (e.g. "[L2]") to queries. If L1/L2 returns nothing,',
        "retry with [L3] -- verbose but correct.\n",
        "### Required Searches\n",
    ]
    cats = [
        ("Own Domain", profile.own_domain, "app overview architecture",
         "Your primary domain knowledge"),
        ("Other Domains", profile.other_domains, "cross-domain patterns",
         "Knowledge from other domains"),
        ("Task Context", profile.task, td,
         "Context for your current task"),
        ("Work History", profile.history, "session history decisions",
         "Prior session history and decisions"),
    ]
    for i, (heading, level, query, desc) in enumerate(cats, 1):
        ld = _LEVEL_DESC[level]
        lines.append(
            f'{i}. **{heading}** ({ld}): Search for '
            f'"{query} [{level.value}]" with project="{proj}"'
        )
        lines.append(f"   -- {desc}. Retrieve at {level.value} for {ld}.\n")
    lines.extend([
        "### Degradation Policy\n",
        "- **No caps, no truncation** -- receive everything your profile specifies.",
        "- If L1/L2 returns empty, fall back to L3.",
        "- Never serve stale summaries -- if L1/L2 seems outdated, request L3.\n",
        "Review all results. Use to understand architecture, avoid prior mistakes,",
        "and follow established patterns. If no results, proceed normally.\n",
    ])
    result = "\n".join(lines)
    try:
        xp = _cross_project_section(td, proj)
        if xp:
            result += xp
    except Exception:  # noqa: BLE001
        logger.debug("Cross-project section failed; skipping")
    result += "---\n"
    return result


def generate_epilogue(agent_type: str, config: ConfigData | None = None) -> str:
    """Generate post-work knowledge capture instructions."""
    proj = _project_key(config)
    _ = agent_type  # reserved for future per-type epilogue variation
    return f"""\

---

## Agent Protocol -- Post-Work Knowledge Capture

After completing your task, save non-obvious discoveries to claude-mem
using save_memory with project="{proj}". Save memories for:

1. **Non-obvious root causes**: Why something was broken and the fix.
2. **Architectural decisions**: Why approach A was chosen over B.
3. **Gotchas and pitfalls**: Hidden dependencies, edge cases, naming quirks.
4. **Cross-layer findings**: Non-obvious inter-layer dependencies.
5. **Codebase patterns**: Recurring patterns future agents should follow.

Keep memories concise. Include file paths and line numbers.
Do NOT save trivial findings easily discoverable via grep/search.

### Three-Level Memory Protocol (US-035)

Write FULL DETAIL (L3) only. The system auto-generates L1 (<20 words) and
L2 (<100 words) from your L3 content post-save. Include file paths, line
numbers, and reasoning. Summarisation happens automatically."""


_DEGRADED_PREAMBLE = """\
## Agent Protocol -- Memory Unavailable (Degraded Mode)

The claude-mem plugin is unavailable. Proceed without loading prior
knowledge. Rely on the codebase itself for context.
Do NOT call claude-mem search or save_memory tools -- they will fail.

---
"""

_DEGRADED_EPILOGUE = """\

---

## Agent Protocol -- Memory Unavailable (Degraded Mode)

claude-mem is unavailable. Skip post-work knowledge capture.
Do NOT call save_memory -- it will fail. Include important discoveries
in your final output so the calling system can capture them."""


def _mem_available() -> bool:
    """Check if claude-mem is available. Returns True if unknown."""
    try:
        from factory.integrations.health import is_up  # noqa: PLC0415
        return is_up("mem")  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001
        return True


def build_agent_prompt(
    agent_type: str,
    task_context: dict[str, Any],
    config: ConfigData | None = None,
    *,
    role_prompt: str = "",
) -> str:
    """Assemble full prompt: preamble + role prompt + epilogue.

    *agent_type*: key like "test-writer", "sa-compute", "crucible".
    *task_context*: dict with ``task_description`` (str) and optional
    ``role_prompt`` override. *config*: optional ConfigData for project key.
    *role_prompt*: core agent instructions inserted between preamble/epilogue.
    """
    if _mem_available():
        preamble = generate_preamble(agent_type, task_context, config)
        epilogue = generate_epilogue(agent_type, config)
    else:
        preamble = _DEGRADED_PREAMBLE
        epilogue = _DEGRADED_EPILOGUE
    body = task_context.get("role_prompt", role_prompt)
    return f"{preamble}\n{body}\n{epilogue}"
