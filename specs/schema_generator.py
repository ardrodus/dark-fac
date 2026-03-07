"""Schema generator — port of generate-schema.sh."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.setup.project_analyzer import AnalysisResult

from dark_factory.specs.base import run_generate, save_artifact, strip_fences
from dark_factory.specs.design_generator import DesignResult

logger = logging.getLogger(__name__)
_NOSQL_KW = ("mongodb", "mongo", "dynamodb", "couchbase", "cassandra", "redis", "firestore")
_PG_KW = ("postgresql", "postgres", "pg", "psycopg", "asyncpg", "pgx", "npgsql")
_MYSQL_KW = ("mysql", "mariadb", "pymysql", "mysqlclient", "mysql2")
_SQLITE_KW = ("sqlite", "sqlite3", "better-sqlite")
_MSSQL_KW = ("mssql", "sqlserver", "sql server", "tedious", "pyodbc")
_ORM_FW = frozenset(("django", "rails", "sqlalchemy", "hibernate", "typeorm",
                      "sequelize", "prisma", "drizzle", "activerecord"))


class SchemaType(Enum):
    SQL = "sql"
    ORM = "orm"
    NOSQL = "nosql"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class SchemaResult:
    """Structured output of schema generation."""
    schema_type: SchemaType
    db_engine: str
    content: str
    validation_passed: bool
    validation_messages: tuple[str, ...] = field(default_factory=tuple)
    output_path: str = ""
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _detect_sql_engine(text: str) -> str:
    for kws, eng in ((_PG_KW, "postgresql"), (_MYSQL_KW, "mysql"),
                     (_SQLITE_KW, "sqlite"), (_MSSQL_KW, "mssql")):
        if any(k in text for k in kws):
            return eng
    return "postgresql"


def _detect_db(design: DesignResult, analysis: object) -> tuple[SchemaType, str]:
    text = " ".join(design.data_model_changes + design.architecture_decisions
                    + design.component_changes).lower()
    fw = getattr(analysis, "framework", "").lower()
    has_db = getattr(analysis, "has_database", False)
    if any(k in text or k in fw for k in _NOSQL_KW):
        eng = next((k for k in ("mongodb", "dynamodb", "couchbase", "cassandra",
                                "redis", "firestore") if k in text or k in fw), "mongodb")
        return SchemaType.NOSQL, eng
    if fw in _ORM_FW or any(k in text for k in _ORM_FW):
        return SchemaType.ORM, _detect_sql_engine(text)
    if has_db or any(k in text for k in _PG_KW + _MYSQL_KW + _SQLITE_KW + _MSSQL_KW):
        return SchemaType.SQL, _detect_sql_engine(text)
    if any(k in text for k in ("table", "column", "schema", "migration", "ddl", "foreign key")):
        return SchemaType.SQL, "postgresql"
    return SchemaType.NONE, ""


# Validation: (regex, message, is_error)
_SQL_CHECKS: list[tuple[str, str, bool]] = [
    (r"(?i)CREATE\s+TABLE", "Missing CREATE TABLE statement", True),
    (r"(?i)(INTEGER|INT\b|VARCHAR|TEXT|BOOLEAN|TIMESTAMP|UUID|SERIAL|BIGINT|DECIMAL|FLOAT|DATE)",
     "No column type definitions found", True),
    (r"(?i)(-- UP|-- Migration Up|-- Forward|-- Begin Migration)",
     "Missing UP migration marker", False),
    (r"(?i)(-- DOWN|-- Migration Down|-- Rollback|DROP TABLE)",
     "Missing DOWN migration marker", False),
]
_NOSQL_CHECKS: list[tuple[str, str, bool]] = [
    (r"(?i)(collection|document|structure|schema|example)",
     "Missing document structure specification", True),
]


def _validate(content: str, st: SchemaType) -> tuple[bool, list[str]]:
    if not content.strip():
        return False, ["Schema content is empty"]
    checks = _SQL_CHECKS if st in (SchemaType.SQL, SchemaType.ORM) else _NOSQL_CHECKS
    msgs = [f"{'FAIL' if err else 'WARN'}: {msg}"
            for pat, msg, err in checks if not re.search(pat, content)]
    return not any(m.startswith("FAIL") for m in msgs), msgs


def _build_prompt(design: DesignResult, st: SchemaType, engine: str, issue: int | str) -> str:
    dm = "\n".join(f"- {c}" for c in design.data_model_changes) or "None specified."
    arch = "\n".join(f"- {d}" for d in design.architecture_decisions)
    comp = "\n".join(f"- {c}" for c in design.component_changes)
    if st == SchemaType.NOSQL:
        fmt = (f"Generate a **NoSQL Document Schema** for {engine}.\n"
               "Output JSON: schema_version, collections (name, document_structure, "
               "indexes, example_documents, validation_rules). Use JSON Schema types.")
    else:
        orm = ("\nInclude ORM entity mapping as SQL comments after DDL."
               if st == SchemaType.ORM else "")
        fmt = (f"Generate **SQL DDL** targeting {engine}.\n"
               "Include `-- UP Migration` with CREATE TABLE (columns, types, constraints, "
               f"indexes), `-- DOWN Migration` with DROP statements.{orm}\n"
               f"Use proper {engine} types.")
    return (
        "You are a Schema Specification Generator.\n\n## Task\n\n"
        f"Produce a database schema for issue #{issue}.\n\n"
        f"## Format\n\n{fmt}\n\n## Data Model Changes\n\n{dm}\n\n"
        f"## Architecture\n\n{arch}\n\n## Components\n\n{comp}\n\n"
        "## Rules\n\n"
        "1. Extract ALL tables/collections from data model changes.\n"
        "2. Include every column with proper types and constraints.\n"
        "3. Include ALL constraints: NOT NULL, UNIQUE, FK, CHECK, DEFAULT.\n"
        "4. Include indexes for query performance.\n"
        "5. Order tables so referenced tables come first.\n"
        "6. Include both UP and DOWN migrations.\n"
        "7. Do NOT invent tables/columns not in the design.\n"
        "8. Output ONLY the raw schema — no markdown fences, no preamble.\n"
        "9. If the feature does not involve database changes, respond with"
        " NO_OPINION_NEEDED. Do not manufacture schema artifacts when the feature"
        " is entirely outside your domain.\n")


def _process(raw: str, st: SchemaType, engine: str, issue_number: int | str,
             state_dir: Path | None) -> SchemaResult:
    content = strip_fences(raw)
    if st == SchemaType.NOSQL:
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("NoSQL schema is not valid JSON: %s", exc)
    passed, msgs = _validate(content, st)
    ext = ".json" if st == SchemaType.NOSQL else ".sql"
    out = save_artifact(content, f"schema{ext}", issue_number, state_dir=state_dir)
    return SchemaResult(schema_type=st, db_engine=engine, content=content,
                        validation_passed=passed, validation_messages=tuple(msgs),
                        output_path=str(out), raw_output=raw)


def _err(st: SchemaType, engine: str, raw: str = "", e: str = "") -> SchemaResult:
    return SchemaResult(schema_type=st, db_engine=engine, content="",
                        validation_passed=False, raw_output=raw,
                        errors=(e,) if e else ())


def generate_schema(  # noqa: PLR0913
    design: DesignResult, analysis: AnalysisResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None, issue_number: int | str = 0,
) -> SchemaResult:
    """Generate database schema DDL and migration scripts."""
    st, engine = _detect_db(design, analysis)
    if st == SchemaType.NONE:
        return SchemaResult(schema_type=st, db_engine="", content="",
                            validation_passed=True,
                            validation_messages=("No database detected — skipped",))
    return run_generate(
        "Schema", _build_prompt(design, st, engine, issue_number),
        lambda raw: _process(raw, st, engine, issue_number, state_dir),
        lambda raw, e: _err(st, engine, raw, e),
        invoke_fn=invoke_fn,
    )
