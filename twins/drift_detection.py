"""Twin drift detection — detect when twins diverge from specs (US-706).

Port of ``twin-drift-detection.sh``.  Compares registered twins against current
spec artifacts to find schema changes, config mismatches, and version drift.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.pipeline.tdd.test_writer import SpecBundle
    from factory.twins.registry import Twin, TwinRegistry

logger = logging.getLogger(__name__)

_EXPECTED_IMAGES: dict[str, str] = {
    "wiremock": "wiremock/wiremock:3.3.1", "postgres": "postgres:16-alpine",
    "mysql": "mysql:8.0", "mariadb": "mariadb:11",
    "mssql": "mcr.microsoft.com/mssql/server:2022-latest",
}


class DriftType(Enum):
    SCHEMA_CHANGE = "schema_change"
    CONFIG_MISMATCH = "config_mismatch"
    VERSION_MISMATCH = "version_mismatch"


@dataclass(frozen=True, slots=True)
class DriftFinding:
    twin_name: str
    drift_type: str  # one of DriftType values
    detail: str


def _extract_endpoints(api_contract: str) -> set[str]:
    """Extract ``METHOD /path`` pairs from an OpenAPI / GraphQL spec."""
    eps: set[str] = set()
    in_paths, cur = False, ""
    for line in api_contract.splitlines():
        s = line.rstrip()
        if re.match(r"^paths:\s*$", s):
            in_paths = True
            continue
        if in_paths and s and not s[0].isspace():
            break
        if not in_paths:
            continue
        pm = re.match(r"^  (/\S*):\s*$", s)
        if pm:
            cur = pm.group(1)
            continue
        mm = re.match(r"^    (get|post|put|patch|delete|options|head):\s*$", s)
        if mm and cur:
            eps.add(f"{mm.group(1).upper()} {cur}")
    for m in re.finditer(r"type\s+(?:Query|Mutation)\s*\{(.*?)\}", api_contract, re.S):
        for fm in re.finditer(r"(\w+)\s*(?:\([^)]*\))?\s*:", m.group(1)):
            eps.add(f"GRAPHQL {fm.group(1)}")
    return eps


def _extract_tables(schema_spec: str) -> set[str]:
    """Extract table names from DDL ``CREATE TABLE`` statements."""
    return {
        m.group(1).lower()
        for m in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?",
            schema_spec, re.I,
        )
    }


def _image_from_compose(content: str) -> str:
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("image:"):
            return s.split(":", 1)[1].strip().strip("\"'")
    return ""


def _check_version(twin: Twin, compose_content: str) -> list[DriftFinding]:
    img = _image_from_compose(compose_content)
    if not img:
        return []
    for key, expected in _EXPECTED_IMAGES.items():
        if key in img and img != expected:
            return [DriftFinding(twin.name, DriftType.VERSION_MISMATCH.value,
                                 f"Image '{img}' differs from expected '{expected}'")]
    return []


def _check_config(twin: Twin) -> list[DriftFinding]:
    if not twin.compose_file:
        return [DriftFinding(twin.name, DriftType.CONFIG_MISMATCH.value,
                             "No compose_file configured")]
    if not Path(twin.compose_file).is_file():
        return [DriftFinding(twin.name, DriftType.CONFIG_MISMATCH.value,
                             f"Compose file missing: {twin.compose_file}")]
    return []


def detect_drift(
    registry: TwinRegistry, specs: SpecBundle,
) -> list[DriftFinding]:
    """Detect drift between registered twins and current spec artifacts.

    Compares API endpoints and DB tables from *specs* against what is
    recorded in *registry*.  Non-empty results indicate that the
    corresponding twin(s) should be regenerated.
    """
    findings: list[DriftFinding] = []
    twins = registry.list()

    # API contract vs registered API twins
    spec_eps = _extract_endpoints(specs.api_contract) if specs.api_contract else set()
    api_twins = [t for t in twins if t.type == "api"]
    if spec_eps and not api_twins:
        findings.append(DriftFinding(
            "(none)", DriftType.SCHEMA_CHANGE.value,
            f"API contract has {len(spec_eps)} endpoint(s) but no API twin registered",
        ))

    # Schema spec vs registered DB twins
    spec_tbl = _extract_tables(specs.schema_spec) if specs.schema_spec else set()
    db_twins = [t for t in twins if t.type == "db"]
    if spec_tbl and not db_twins:
        findings.append(DriftFinding(
            "(none)", DriftType.SCHEMA_CHANGE.value,
            f"Schema has {len(spec_tbl)} table(s) but no DB twin registered",
        ))

    # Config + version per twin
    for twin in twins:
        cfg = _check_config(twin)
        findings.extend(cfg)
        if cfg:
            continue
        try:
            content = Path(twin.compose_file).read_text(encoding="utf-8")
        except OSError:
            continue
        findings.extend(_check_version(twin, content))

    logger.info("Drift scan: %d finding(s) across %d twin(s)", len(findings), len(twins))
    return findings
