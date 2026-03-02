"""Agent Protocol — prompt assembly for every agent invocation."""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dark_factory.core.config_manager import ConfigData

logger = logging.getLogger(__name__)

# ── Role-to-context-level mapping ────────────────────────────────
# Each role gets one level: "full", "summary", or "minimal".
# Flat dict replaces the former L1/L2/L3 zoom system + alias indirection.

ROLE_CONTEXT: dict[str, str] = {
    "sa-specialist": "summary", "sa-compute": "summary",
    "sa-storage": "summary", "sa-database": "summary",
    "sa-network": "summary", "sa-analytics": "summary",
    "sa-mlai": "summary", "sa-devops": "summary",
    "sa-monitoring": "summary", "sa-integration": "summary",
    "sa-security": "summary", "sa-code-quality": "summary",
    "sa-performance": "summary", "sa-testing": "summary",
    "sa-dependencies": "summary", "sa-api-design": "summary",
    "sa-ux": "summary", "sa-lead": "summary",
    "sa-frontend": "summary", "sa-backend": "summary",
    "sa-database-web": "summary", "sa-security-web": "summary",
    "sa-performance-web": "summary", "sa-integration-web": "summary",
    "sa-lead-web": "summary",
    "sa-security-console": "summary", "sa-performance-console": "summary",
    "sa-integration-console": "summary", "sa-lead-console": "summary",
    "test-writer": "full", "eng-test-writer": "full",
    "feature-writer": "full", "eng-feature-writer": "full",
    "crucible": "full", "eng-crucible": "full", "code-review": "full",
    "learning": "minimal",
}

_LEARNING_RE = re.compile(r"^learning(?:-.+)?$")
_LEVEL_HINT = {"full": "full detail", "summary": "concise summaries", "minimal": "taglines only"}


def get_context_level(agent_type: str) -> str:
    """Return the context level for *agent_type*."""
    if agent_type in ROLE_CONTEXT:
        return ROLE_CONTEXT[agent_type]
    if agent_type and _LEARNING_RE.match(agent_type):
        return "minimal"
    return "full"


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
        f"**Search each project**:\n{searches}\n\n"
        "**Ranking**: relevance -> confidence -> recency. "
        "**Cap**: 5 cross-project patterns max.\n"
        "**Degradation**: If search fails, fall back to project-only.\n"
    )


def generate_preamble(agent_type: str, task_context: dict[str, Any] | None = None,
                      config: ConfigData | None = None) -> str:
    """Generate pre-work context loading instructions."""
    level = get_context_level(agent_type)
    proj = _project_key(config)
    label = agent_type or "default"
    td = (task_context or {}).get("task_description", "current task")
    hint = _LEVEL_HINT[level]
    lines = [
        "## Agent Protocol -- Pre-Work Context Loading\n",
        f"Search claude-mem FIRST. **Context Level: {label}** -- retrieve {hint}.\n",
        "### Required Searches\n",
        f'1. **Own Domain**: Search for "app overview architecture" with project="{proj}"',
        f'2. **Other Domains**: Search for "cross-domain patterns" with project="{proj}"',
        f'3. **Task Context**: Search for "{td}" with project="{proj}"',
        f'4. **Work History**: Search for "session history decisions" with project="{proj}"\n',
        "If a search returns empty, proceed without it. If results seem stale,",
        "request full detail. Review all results before starting work.\n",
    ]
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
    _ = agent_type
    return (
        "\n---\n\n## Agent Protocol -- Post-Work Knowledge Capture\n\n"
        f'Save non-obvious discoveries to claude-mem (project="{proj}"):\n\n'
        "1. **Non-obvious root causes**: Why something was broken and the fix.\n"
        "2. **Architectural decisions**: Why approach A was chosen over B.\n"
        "3. **Gotchas and pitfalls**: Hidden dependencies, edge cases.\n"
        "4. **Cross-layer findings**: Non-obvious inter-layer dependencies.\n"
        "5. **Codebase patterns**: Recurring patterns future agents should follow.\n\n"
        "Keep memories concise with file paths and line numbers.\n\n"
        "### Three-Level Memory Protocol (US-035)\n\n"
        "Write FULL DETAIL only. The system auto-generates summaries and\n"
        "taglines post-save. Include file paths, line numbers, and reasoning."
    )


_DEGRADED_PREAMBLE = (
    "## Agent Protocol -- Memory Unavailable (Degraded Mode)\n\n"
    "claude-mem is unavailable. Proceed without loading prior knowledge.\n"
    "Do NOT call claude-mem search or save_memory tools -- they will fail.\n\n---\n"
)

_DEGRADED_EPILOGUE = (
    "\n---\n\n## Agent Protocol -- Memory Unavailable (Degraded Mode)\n\n"
    "claude-mem is unavailable. Skip post-work knowledge capture.\n"
    "Do NOT call save_memory -- it will fail. Include important discoveries\n"
    "in your final output so the calling system can capture them."
)


def _mem_available() -> bool:
    """Check if claude-mem is available. Returns True if unknown."""
    try:
        from dark_factory.integrations.health import is_up  # type: ignore[import-not-found]  # noqa: PLC0415
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
    """Assemble full prompt: preamble + role prompt + epilogue."""
    if _mem_available():
        preamble = generate_preamble(agent_type, task_context, config)
        epilogue = generate_epilogue(agent_type, config)
    else:
        preamble = _DEGRADED_PREAMBLE
        epilogue = _DEGRADED_EPILOGUE
    body = task_context.get("role_prompt", role_prompt)
    return f"{preamble}\n{body}\n{epilogue}"
