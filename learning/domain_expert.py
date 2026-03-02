"""Domain Expert agent — discovers domain entities, business rules, and bounded contexts."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.workspace.manager import Workspace
    from factory.learning.scout import ScoutResult

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT, _STATE_DIR = 300, Path(".dark-factory")
_SRC_EXTS = frozenset((".py", ".js", ".ts", ".go", ".rs", ".java", ".cs", ".kt", ".rb"))
_EXCLUDE = frozenset(
    "node_modules .git vendor dist __pycache__ .tox .venv .mypy_cache coverage build "
    "target .next .nuxt venv env .dark-factory".split())
_MODEL_KW = (
    "model", "entity", "domain", "aggregate", "value_object", "enum", "schema",
    "record", "struct", "class", "type", "interface",
)
_RULE_KW = (
    "validate", "policy", "rule", "constraint", "guard", "check", "require",
    "permission", "authorize", "approval", "workflow", "state_machine",
)
_DOMAIN_DIRS = (
    "models", "entities", "domain", "core", "schemas", "types", "enums",
    "aggregates", "value_objects", "rules", "policies", "services",
)


@dataclass(frozen=True, slots=True)
class DomainExpertResult:
    """Structured output of the Domain Expert agent."""
    domain_entities: tuple[str, ...] = ()
    business_rules: tuple[str, ...] = ()
    domain_language: tuple[tuple[str, str], ...] = ()
    bounded_contexts: tuple[str, ...] = ()
    raw_output: str = ""
    errors: tuple[str, ...] = ()


def _rd(p: Path, limit: int = 6000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""

def _strs(data: dict[str, object], key: str) -> tuple[str, ...]:
    raw = data.get(key)
    return tuple(str(e) for e in raw) if isinstance(raw, list) else ()

def _pairs(data: dict[str, object], key: str) -> tuple[tuple[str, str], ...]:
    raw = data.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(
        (str(e[0]), str(e[1])) for e in raw
        if isinstance(e, (list, tuple)) and len(e) >= 2  # noqa: PLR2004
    )

def _collect_context(ws: Path, scout: ScoutResult) -> str:
    parts: list[str] = [f"## Scout Overview\n{scout.app_overview}\n"]
    if scout.key_abstractions:
        parts.append(f"Key abstractions: {', '.join(scout.key_abstractions)}\n")
    if scout.entry_points:
        parts.append(f"Entry points: {', '.join(scout.entry_points)}\n")
    # Scan domain-relevant directories
    for dname in _DOMAIN_DIRS:
        dd = ws / dname
        if not dd.is_dir():
            for sub in ("src", "lib", "app"):
                dd = ws / sub / dname
                if dd.is_dir():
                    break
            else:
                continue
        for f in sorted(dd.rglob("*"))[:20]:
            if f.is_file() and f.suffix.lower() in _SRC_EXTS:
                parts.append(f"## {f.relative_to(ws)}\n{_rd(f)}\n")
    # Find files with domain/model keywords in filenames
    seen: set[Path] = set()
    for f in ws.rglob("*"):
        if not (f.is_file() and f.suffix.lower() in _SRC_EXTS):
            continue
        if any(p in _EXCLUDE for p in f.parts):
            continue
        stem = f.stem.lower()
        if any(kw in stem for kw in _MODEL_KW[:6]):
            if f not in seen and len(seen) < 15:  # noqa: PLR2004
                seen.add(f)
                parts.append(f"## {f.relative_to(ws)}\n{_rd(f)}\n")
    # Find files with business-rule keywords in content
    for f in ws.rglob("*"):
        if not (f.is_file() and f.suffix.lower() in _SRC_EXTS):
            continue
        if any(p in _EXCLUDE for p in f.parts):
            continue
        if f in seen:
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        if sum(1 for kw in _RULE_KW if kw in content.lower()) >= 2 and len(seen) < 25:  # noqa: PLR2004
            seen.add(f)
            parts.append(f"## {f.relative_to(ws)}\n{content}\n")
    return "\n".join(parts)

def _build_prompt(context: str) -> str:
    return f"""\
You are the Domain Expert Agent. Analyze the workspace data below and produce a JSON object \
documenting domain concepts, entities, business rules, domain language, and bounded contexts.

{context}

## Output Format (strict JSON)
{{"domain_entities": ["User", "Order", "Product", ...],\
 "business_rules": ["Orders must have at least one line item", ...],\
 "domain_language": [["term", "definition"], ...],\
 "bounded_contexts": ["Identity & Auth", "Order Management", ...]}}

Guidelines:
- domain_entities: class names or concepts that represent core domain objects
- business_rules: invariants, constraints, validations, policies found in the code
- domain_language: ubiquitous language terms with short definitions
- bounded_contexts: logical groupings of related domain concepts
Output ONLY the JSON object, no markdown fences."""

def _invoke_agent(
    prompt: str, workspace_path: str, *,
    invoke_fn: Callable[[str], str] | None = None,
) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415
    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, check=True, cwd=workspace_path,
    ).stdout

def _parse_result(raw: str) -> DomainExpertResult:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\"domain_entities\".*\}", text, re.DOTALL)
    if not match:
        match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return DomainExpertResult(raw_output=raw, errors=("no JSON found in output",))
    data: dict[str, object] = json.loads(match.group(0))
    return DomainExpertResult(
        domain_entities=_strs(data, "domain_entities"),
        business_rules=_strs(data, "business_rules"),
        domain_language=_pairs(data, "domain_language"),
        bounded_contexts=_strs(data, "bounded_contexts"),
        raw_output=raw,
    )

def _save(result: DomainExpertResult, repo_name: str, *, state_dir: Path | None = None) -> Path:
    sd = (state_dir or _STATE_DIR) / "learning" / repo_name
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "domain_expert.json"
    payload = {k: v for k, v in asdict(result).items() if k != "raw_output"}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Domain Expert results saved to %s", out)
    return out

def run_domain_expert(
    workspace: Workspace, scout: ScoutResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> DomainExpertResult:
    """Discover domain entities, business rules, domain language, and bounded contexts."""
    ws_path = Path(workspace.path)
    repo_name = workspace.name or ws_path.name
    context = _collect_context(ws_path, scout)
    prompt = _build_prompt(context)
    try:
        raw = _invoke_agent(prompt, workspace.path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Domain Expert agent failed: %s", exc)
        return DomainExpertResult(raw_output="", errors=(str(exc),))
    try:
        result = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Domain Expert parse error: %s", exc)
        return DomainExpertResult(raw_output=raw, errors=(f"parse error: {exc}",))
    _save(result, repo_name, state_dir=state_dir)
    return result
