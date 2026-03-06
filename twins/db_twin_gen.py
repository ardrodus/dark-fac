"""Database twin generation — DDL, seed data, and compose from schema specs (US-705).

Port of ``database-seed-gen.sh``.  Generates DB twin containers with init
scripts (DDL + seed data) mounted into ``/docker-entrypoint-initdb.d/``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from dark_factory.specs.schema_generator import SchemaResult, SchemaType

logger = logging.getLogger(__name__)

_IMAGES: dict[str, str] = {
    "postgresql": "postgres:16-alpine", "mysql": "mysql:8.0",
    "mariadb": "mariadb:11", "sqlite": "",
    "mssql": "mcr.microsoft.com/mssql/server:2022-latest",
}
_ENGINE_CFG: dict[str, dict[str, Any]] = {
    "postgresql": {"port": "5432",
        "env": {"POSTGRES_DB": "testdb", "POSTGRES_USER": "twin",
                "POSTGRES_PASSWORD": "twin"},
        "hc": "pg_isready -U twin -d testdb"},
    "mysql": {"port": "3306",
        "env": {"MYSQL_DATABASE": "testdb", "MYSQL_USER": "twin",
                "MYSQL_PASSWORD": "twin", "MYSQL_ROOT_PASSWORD": "rootpw"},
        "hc": "mysqladmin ping -h 127.0.0.1 -u twin -ptwin"},
    "mariadb": {"port": "3306",
        "env": {"MARIADB_DATABASE": "testdb", "MARIADB_USER": "twin",
                "MARIADB_PASSWORD": "twin", "MARIADB_ROOT_PASSWORD": "rootpw"},
        "hc": "mariadb-admin ping -h 127.0.0.1 -u twin -ptwin"},
    "mssql": {"port": "1433",
        "env": {"ACCEPT_EULA": "Y", "MSSQL_SA_PASSWORD": "Twin@1234"},
        "hc": '/opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P "Twin@1234" -Q "SELECT 1"'},
}
_SEED_ROWS = 15
_NAMES = ("Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace",
          "Hank", "Iris", "Jack", "Karen", "Leo", "Mia", "Noah", "Olivia")
_SURNAMES = ("Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
             "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez",
             "Lopez", "Gonzalez", "Wilson", "Anderson")


@dataclass(frozen=True, slots=True)
class DbTwinConfig:
    """Result of database twin generation."""
    compose_fragment: str
    init_sql: str
    seed_sql: str
    service_name: str = ""
    db_engine: str = ""
    env_overrides: dict[str, str] = field(default_factory=dict)
    errors: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class _Column:
    name: str
    col_type: str
    nullable: bool = True


def _parse_tables(ddl: str) -> list[tuple[str, tuple[_Column, ...]]]:
    tables: list[tuple[str, tuple[_Column, ...]]] = []
    for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?"
        r"\s*\((.*?)\)\s*;", ddl, re.S | re.I,
    ):
        cols: list[_Column] = []
        for line in m.group(2).split(","):
            line = line.strip()
            if not line or re.match(r"(?i)(PRIMARY|UNIQUE|CHECK|CONSTRAINT|INDEX|FOREIGN)", line):
                continue
            parts = line.split()
            if len(parts) >= 2:
                cname = parts[0].strip("`\"")
                ctype = parts[1].upper()
                if any(k in ctype for k in ("SERIAL", "IDENTITY", "AUTO_INCREMENT")):
                    continue
                cols.append(_Column(name=cname, col_type=ctype, nullable="NOT NULL" not in line.upper()))
        if cols:
            tables.append((m.group(1), tuple(cols)))
    return tables


def _val(col: _Column, i: int) -> str:
    lo, t = col.name.lower(), col.col_type
    if "email" in lo:
        return f"'user{i}@example.com'"
    if lo in ("name", "first_name", "firstname"):
        return f"'{_NAMES[i % len(_NAMES)]}'"
    if lo in ("last_name", "lastname", "surname"):
        return f"'{_SURNAMES[i % len(_SURNAMES)]}'"
    if "phone" in lo:
        return f"'+1-555-{100 + i:04d}'"
    if "url" in lo or "website" in lo:
        return f"'https://example.com/r/{i}'"
    if "status" in lo:
        return f"'{('active', 'inactive', 'pending')[i % 3]}'"
    if re.search(r"(?i)uuid", t):
        return f"'{i:08d}-9c0b-4ef8-bb6d-6bb9bd380a{i:02d}'"
    if re.search(r"(?i)(int|bigint|smallint|numeric|decimal|float|double|real)", t):
        return str(i + 1) if re.search(r"(?i)int", t) else f"{(i + 1) * 10.5:.2f}"
    if re.search(r"(?i)bool", t):
        return "TRUE" if i % 2 == 0 else "FALSE"
    if re.search(r"(?i)(timestamp|datetime)", t):
        return f"'2025-01-{i % 28 + 1:02d} 10:30:00'"
    if re.search(r"(?i)date", t):
        return f"'2025-01-{i % 28 + 1:02d}'"
    if re.search(r"(?i)json", t):
        return "'{}'"
    return f"'value_{i}'"


def _seed_sql(tables: list[tuple[str, tuple[_Column, ...]]]) -> str:
    if not tables:
        return "-- No tables found -- no seed data generated.\n"
    parts: list[str] = ["-- Seed data: realistic representative records", ""]
    for tname, cols in tables:
        names = ", ".join(c.name for c in cols)
        parts.append(f"-- Table: {tname}")
        parts.append(f"INSERT INTO {tname} ({names}) VALUES")
        rows = [f"    ({', '.join(_val(c, i) for c in cols)})" for i in range(_SEED_ROWS)]
        parts.append(",\n".join(rows) + ";")
        parts.append("")
    return "\n".join(parts)


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).lower()


def _compose(name: str, engine: str) -> str:
    s = _slug(name)
    image = _IMAGES.get(engine, "postgres:16-alpine")
    if not image:
        return f"  # {engine} does not require a container twin\n"
    cfg = _ENGINE_CFG.get(engine, _ENGINE_CFG["postgresql"])
    env = "\n".join(f"      {k}: \"{v}\"" for k, v in cfg["env"].items())
    return (
        f"  df-twin-{s}:\n    image: {image}\n"
        f"    ports:\n      - \"{cfg['port']}\"\n"
        f"    environment:\n{env}\n"
        f"    volumes:\n"
        f"      - ./twin-data/{s}/init:/docker-entrypoint-initdb.d\n"
        f"    healthcheck:\n"
        f"      test: [\"CMD-SHELL\", \"{cfg['hc']}\"]\n"
        f"      interval: 5s\n      timeout: 3s\n      retries: 10\n")


def _dsn(engine: str, slug: str) -> str:
    m: dict[str, str] = {
        "postgresql": f"postgresql://twin:twin@df-twin-{slug}:5432/testdb",
        "mysql": f"mysql://twin:twin@df-twin-{slug}:3306/testdb",
        "mariadb": f"mysql://twin:twin@df-twin-{slug}:3306/testdb",
        "mssql": f"mssql://sa:Twin@1234@df-twin-{slug}:1433/testdb",
    }
    return m.get(engine, f"{engine}://twin:twin@df-twin-{slug}/testdb")


def generate_db_twin(schema: SchemaResult, *, service_name: str = "") -> DbTwinConfig:
    """Generate a database twin config from a schema spec.

    Produces DDL init script, seed data SQL, and a docker-compose fragment
    for postgres, mysql, mariadb, mssql, or sqlite schemas.
    """
    svc = service_name or "app-db"
    engine = schema.db_engine or "postgresql"
    s = _slug(svc)
    if schema.schema_type == SchemaType.NONE or not schema.content.strip():
        msg = "No database schema detected." if schema.schema_type == SchemaType.NONE else "Schema content is empty."
        return DbTwinConfig(compose_fragment="", init_sql="", seed_sql="",
                            service_name=svc, db_engine=engine, errors=(msg,))
    content = schema.content.strip()
    init = f"-- DDL init for {svc} ({engine})\n-- Auto-generated by Dark Factory\n\n{content}\n"
    tables = _parse_tables(content)
    seed = _seed_sql(tables)
    logger.info("Generated DB twin '%s' (%s): %d tables", svc, engine, len(tables))
    return DbTwinConfig(
        compose_fragment=_compose(svc, engine), init_sql=init, seed_sql=seed,
        service_name=svc, db_engine=engine,
        env_overrides={"DATABASE_URL": _dsn(engine, s)},
    )
