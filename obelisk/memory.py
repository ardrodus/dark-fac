"""Obelisk memory — save/retrieve pattern memories via claude-mem MCP."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from factory.integrations.shell import run_command

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.knowledge.patterns import Pattern

logger = logging.getLogger(__name__)

_MCP_TIMEOUT = 30
_DEFAULT_PROJECT = "obelisk-memory"
_SEARCH_LIMIT = 20
_META_MARKER = "__meta__="

def _pattern_to_mem_text(pattern: Pattern, context: str = "") -> str:
    """Serialize a Pattern to a claude-mem text entry with embedded metadata."""
    meta: dict[str, Any] = {
        "name": pattern.name, "type": pattern.type,
        "confidence": pattern.confidence, "source_repo": pattern.source_repo,
        "tags": pattern.tags, "usage_count": pattern.usage_count,
        "created_at": pattern.created_at, "last_used_at": pattern.last_used_at,
    }
    if context:
        meta["context"] = context
    return (
        f"[pattern] {pattern.name} (type={pattern.type}, "
        f"confidence={pattern.confidence:.2f})\n"
        f"source_repo={pattern.source_repo}\ntags={','.join(pattern.tags)}\n"
        f"---\n{pattern.content}\n---\n"
        f"{_META_MARKER}{json.dumps(meta, separators=(',', ':'))}"
    )

def _mem_text_to_pattern_kwargs(text: str) -> dict[str, Any] | None:
    """Extract Pattern constructor kwargs from a claude-mem entry."""
    idx = text.rfind(_META_MARKER)
    if idx < 0:
        return None
    try:
        meta: dict[str, Any] = json.loads(text[idx + len(_META_MARKER):].strip())
    except (json.JSONDecodeError, ValueError):
        return None
    parts = text.split("---")
    content = parts[1].strip() if len(parts) >= 3 else ""  # noqa: PLR2004
    return {
        "name": str(meta.get("name", "")), "type": str(meta.get("type", "")),
        "content": content, "confidence": float(meta.get("confidence", 0.5)),
        "tags": list(meta.get("tags") or []),
        "source_repo": str(meta.get("source_repo", "")),
        "usage_count": int(meta.get("usage_count", 0)),
        "created_at": str(meta.get("created_at", "")),
        "last_used_at": str(meta.get("last_used_at", "")),
    }

def _call_mcp_save(
    text: str, title: str, project: str, *,
    invoke_fn: Callable[[str, str, str], bool] | None = None,
) -> bool:
    """Persist *text* to claude-mem via MCP save_memory."""
    if invoke_fn is not None:
        return invoke_fn(text, title, project)
    payload = json.dumps(
        {"text": text, "title": title, "project": project}, separators=(",", ":"),
    )
    try:
        result = run_command(
            ["claude", "mcp", "call", "claude-mem", "save_memory", "--input", payload],
            timeout=_MCP_TIMEOUT, check=False,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        logger.warning("claude-mem save_memory call failed", exc_info=True)
        return False

def _call_mcp_search(
    query: str, project: str, *, limit: int = _SEARCH_LIMIT,
    invoke_fn: Callable[[str, str, int], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Search claude-mem and return raw result dicts."""
    if invoke_fn is not None:
        return invoke_fn(query, project, limit)
    payload = json.dumps(
        {"query": query, "project": project, "limit": limit}, separators=(",", ":"),
    )
    try:
        result = run_command(
            ["claude", "mcp", "call", "claude-mem", "search", "--input", payload],
            timeout=_MCP_TIMEOUT, check=False,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        if isinstance(data, list):
            return data  # type: ignore[return-value]
        return list(data["results"]) if isinstance(data, dict) and "results" in data else []
    except (json.JSONDecodeError, Exception):  # noqa: BLE001
        logger.warning("claude-mem search call failed", exc_info=True)
        return []

def save_pattern(
    pattern: Pattern, project: str = _DEFAULT_PROJECT, *,
    context: str = "",
    save_fn: Callable[[str, str, str], bool] | None = None,
) -> bool:
    """Save a Pattern to claude-mem for cross-session retrieval."""
    text = _pattern_to_mem_text(pattern, context)
    title = f"[pattern] {pattern.name} ({pattern.type})"
    ok = _call_mcp_save(text, title, project, invoke_fn=save_fn)
    if ok:
        logger.info("Saved pattern %s to claude-mem (project=%s)", pattern.name, project)
    else:
        logger.warning("Failed to save pattern %s to claude-mem", pattern.name)
    return ok

def search_patterns(
    query: str, project: str = _DEFAULT_PROJECT, *,
    limit: int = _SEARCH_LIMIT,
    search_fn: Callable[[str, str, int], list[dict[str, Any]]] | None = None,
) -> list[Pattern]:
    """Search claude-mem for patterns matching *query*."""
    from factory.knowledge.patterns import Pattern as PatternCls  # noqa: PLC0415

    raw_results = _call_mcp_search(query, project, limit=limit, invoke_fn=search_fn)
    patterns: list[Pattern] = []
    for entry in raw_results:
        if isinstance(entry, dict):
            text = str(entry.get("text", entry.get("content", "")))
        elif isinstance(entry, str):
            text = entry
        else:
            continue
        kwargs = _mem_text_to_pattern_kwargs(text) if text else None
        if kwargs is None:
            continue
        try:
            patterns.append(PatternCls(**kwargs))
        except (TypeError, ValueError):
            logger.debug("Skipping unparseable claude-mem entry")
    logger.info("claude-mem search: %d pattern(s) for query=%r", len(patterns), query)
    return patterns
