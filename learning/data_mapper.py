"""Data Mapper agent — discovers data models, DB schema, ORM mappings, and data flow."""
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
_DB_DIRS = (
    "migrations", "migrate", "db", "database", "schema", "prisma", "drizzle",
    "alembic", "knex", "sequelize", "typeorm", "ormconfig",
)
_ORM_KW = (
    "model", "schema", "table", "column", "field", "relationship", "foreign_key",
    "index", "migration", "entity", "repository", "mapper", "orm",
)
_SCHEMA_FILES = (
    "schema.prisma", "schema.graphql", "schema.sql", "schema.rb",
    "ormconfig.json", "ormconfig.ts", "knexfile.js", "knexfile.ts",
    "drizzle.config.ts", "alembic.ini", "database.yml",
)
_SQL_EXTS, _MIGRATION_RE = frozenset((".sql", ".ddl")), re.compile(
    r"create.table|alter.table|add.column|create.index|drop.table"
    r"|migration|Schema\.(create|table)|knex\.schema"
    r"|CREATE\s+TABLE|ALTER\s+TABLE|db\.create_all"
    r"|Base\.metadata|declarative_base|mapped_column", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DataMapperResult:
    """Structured output of the Data Mapper agent."""
    data_models: tuple[str, ...] = ()
    db_schema: tuple[str, ...] = ()
    orm_mappings: tuple[str, ...] = ()
    migration_history: tuple[str, ...] = ()
    data_flow: tuple[tuple[str, str], ...] = ()
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
    for name in _SCHEMA_FILES:
        content = _rd(ws / name)
        if content:
            parts.append(f"## {name}\n{content}\n")
    seen: set[Path] = set()
    for dname in _DB_DIRS:
        for root in (ws, ws / "src", ws / "lib", ws / "app"):
            dd = root / dname
            if not dd.is_dir():
                continue
            for f in sorted(dd.rglob("*"))[:20]:
                if f.is_file() and f not in seen:
                    seen.add(f)
                    parts.append(f"## {f.relative_to(ws)}\n{_rd(f)}\n")
    for f in ws.rglob("*"):
        if not f.is_file() or any(p in _EXCLUDE for p in f.parts):
            continue
        if f.suffix.lower() in _SQL_EXTS and f not in seen and len(seen) < 30:  # noqa: PLR2004
            seen.add(f)
            parts.append(f"## {f.relative_to(ws)}\n{_rd(f)}\n")
    for f in ws.rglob("*"):
        if not (f.is_file() and f.suffix.lower() in _SRC_EXTS):
            continue
        if any(p in _EXCLUDE for p in f.parts) or f in seen:
            continue
        stem = f.stem.lower()
        if any(kw in stem for kw in _ORM_KW[:6]):
            if len(seen) < 40:  # noqa: PLR2004
                seen.add(f)
                parts.append(f"## {f.relative_to(ws)}\n{_rd(f)}\n")
                continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        if _MIGRATION_RE.search(content) and len(seen) < 40:  # noqa: PLR2004
            seen.add(f)
            parts.append(f"## {f.relative_to(ws)}\n{content}\n")
    return "\n".join(parts)

def _build_prompt(context: str) -> str:
    return f"""\
You are the Data Mapper Agent. Analyze the workspace data below and produce a JSON object \
documenting data models, database schema, ORM mappings, migration history, and data flow.

{context}

## Output Format (strict JSON)
{{"data_models": ["User (id, name, email, created_at)", ...],\
 "db_schema": ["users: id PK, name VARCHAR, email VARCHAR UNIQUE", ...],\
 "orm_mappings": ["User model -> users table (SQLAlchemy/Prisma/...)", ...],\
 "migration_history": ["001_create_users: adds users table", ...],\
 "data_flow": [["source", "destination"], ...]}}

Guidelines:
- data_models: model/entity names with their key fields
- db_schema: table definitions with column types and constraints
- orm_mappings: how code models map to database tables, including the ORM used
- migration_history: chronological list of schema migrations found
- data_flow: pairs showing data movement (e.g. ["API input", "users table"])
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

def _parse_result(raw: str) -> DataMapperResult:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\"data_models\".*\}", text, re.DOTALL)
    if not match:
        match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return DataMapperResult(raw_output=raw, errors=("no JSON found in output",))
    data: dict[str, object] = json.loads(match.group(0))
    return DataMapperResult(
        data_models=_strs(data, "data_models"),
        db_schema=_strs(data, "db_schema"),
        orm_mappings=_strs(data, "orm_mappings"),
        migration_history=_strs(data, "migration_history"),
        data_flow=_pairs(data, "data_flow"),
        raw_output=raw,
    )

def _save(result: DataMapperResult, repo_name: str, *, state_dir: Path | None = None) -> Path:
    sd = (state_dir or _STATE_DIR) / "learning" / repo_name
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "data_mapper.json"
    payload = {k: v for k, v in asdict(result).items() if k != "raw_output"}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Data Mapper results saved to %s", out)
    return out

def run_data_mapper(
    workspace: Workspace, scout: ScoutResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> DataMapperResult:
    """Discover data models, DB schema, ORM mappings, migration history, and data flow."""
    ws_path = Path(workspace.path)
    repo_name = workspace.name or ws_path.name
    context = _collect_context(ws_path, scout)
    prompt = _build_prompt(context)
    try:
        raw = _invoke_agent(prompt, workspace.path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Data Mapper agent failed: %s", exc)
        return DataMapperResult(raw_output="", errors=(str(exc),))
    try:
        result = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Data Mapper parse error: %s", exc)
        return DataMapperResult(raw_output=raw, errors=(f"parse error: {exc}",))
    _save(result, repo_name, state_dir=state_dir)
    return result
