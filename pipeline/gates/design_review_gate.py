"""Design review validation gate — validates design docs before engineering.

Port of design-review-gate.sh.  Validates API consistency, data completeness,
interface coherence, and architecture guidance alignment using structured
:class:`DesignResult` and :class:`SpecBundle` inputs.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import builtins

from factory.pipeline.tdd.test_writer import SpecBundle
from factory.specs.design_generator import DesignResult

logger = logging.getLogger(__name__)

_HTTP_METHOD = re.compile(r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S+)", re.I)
_ENTITY_DEF = re.compile(
    r"(?:CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?)"
    r"|(?:(?:class|interface|type|struct|entity|model|table|collection)\s+([A-Z][A-Za-z0-9_]+))",
    re.I,
)
_TYPE_DEF = re.compile(
    r"(?:export\s+)?(?:interface|type|class|struct|trait|enum|Protocol|TypedDict)"
    r"\s+([A-Z][A-Za-z0-9_]+)",
)
_RECOMMEND = re.compile(
    r"(?:recommend|should use|must use|prefer|advised|adopt)\s+([A-Za-z0-9_-]+)", re.I,
)
_SKIP = frozenset([
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "HTTP",
    "API", "REST", "JSON", "XML", "YAML", "NULL", "TRUE", "FALSE",
    "String", "Int", "Integer", "Float", "Double", "Boolean", "Bool",
    "Void", "Error", "Promise", "Result", "Optional", "List", "Array",
    "Map", "Set", "Date", "DateTime",
])


@dataclass(frozen=True, slots=True)
class Finding:
    """Single validation finding."""
    check: str
    severity: str  # "error" | "warning" | "info"
    message: str


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of the design review gate."""
    passed: bool
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    blocking_issues: tuple[Finding, ...] = field(default_factory=tuple)


def _entities(text: str) -> set[str]:
    out: set[str] = set()
    for m in _ENTITY_DEF.finditer(text):
        n = m.group(1) or m.group(2)
        if n and n not in _SKIP:
            out.add(n)
    return out


def _type_defs(text: str) -> set[str]:
    return {m.group(1) for m in _TYPE_DEF.finditer(text) if m.group(1) not in _SKIP}


def _F(check: str, sev: str, msg: str) -> Finding:  # noqa: N802
    return Finding(check=check, severity=sev, message=msg)


# ── Check 1: API consistency ─────────────────────────────────────

def _check_api(design: DesignResult, specs: SpecBundle) -> builtins.list[Finding]:
    findings: builtins.list[Finding] = []
    if not design.api_changes:
        return [_F("api-consistency", "info", "No API changes in design — skipped")]
    eps = [(m.upper(), p) for m, p in _HTTP_METHOD.findall("\n".join(design.api_changes))]
    if not eps:
        return [_F("api-consistency", "info", "No HTTP endpoints in design API changes")]
    seen: set[str] = set()
    for method, path in eps:
        key = f"{method} {path}"
        if key in seen:
            findings.append(_F("api-consistency", "error", f"Duplicate endpoint: {key}"))
        seen.add(key)
    contract = specs.api_contract.strip()
    if not contract:
        findings.append(_F("api-consistency", "warning", "No API contract — cannot cross-reference"))
        return findings
    cpaths: set[str] = set()
    for m in re.finditer(r"(?:^\s+|['\"])(/[a-zA-Z0-9_/{}-]+)", contract, re.M):
        cpaths.add(m.group(1))
    if cpaths:
        def _norm(p: str) -> str:
            return re.sub(r"\{[^}]+\}", "{id}", p)
        cnorm = {_norm(p) for p in cpaths}
        for method, path in eps:
            if path not in cpaths and _norm(path) not in cnorm:
                findings.append(_F("api-consistency", "error",
                                   f"Endpoint {method} {path} not in contract"))
    return findings


# ── Check 2: Data completeness ───────────────────────────────────

def _check_data(design: DesignResult, specs: SpecBundle) -> builtins.list[Finding]:
    if not design.data_model_changes:
        return [_F("data-completeness", "info", "No data model changes — skipped")]
    dm_text = "\n".join(design.data_model_changes)
    ents = _entities(dm_text)
    if not ents:
        return [_F("data-completeness", "warning", "Data model changes but no entity names found")]
    findings: builtins.list[Finding] = []
    for e in sorted(ents):
        pat = re.compile(rf"{re.escape(e)}.*?(?:field|column|attribute|property|:\s*\w+)",
                         re.I | re.S)
        if not pat.search(dm_text):
            findings.append(_F("data-completeness", "warning",
                               f"Entity '{e}' has no field definitions"))
    schema = specs.schema_spec.strip()
    if not schema:
        if ents:
            findings.append(_F("data-completeness", "warning",
                               "No schema spec — cannot validate completeness"))
        return findings
    s_ents = _entities(schema)
    for e in sorted(ents):
        if not any(e.lower() == s.lower() for s in s_ents):
            findings.append(_F("data-completeness", "error",
                               f"Entity '{e}' in design but missing from schema"))
    return findings


# ── Check 3: Interface coherence ─────────────────────────────────

def _detect_cycle(edges: builtins.list[tuple[str, str]]) -> str | None:
    graph: dict[str, builtins.list[str]] = defaultdict(builtins.list)
    for src, tgt in edges:
        graph[src].append(tgt)
    for start in graph:
        visited: set[str] = set()
        queue = builtins.list(graph[start])
        while queue:
            node = queue.pop(0)
            if node == start:
                return f"{start} -> ... -> {start}"
            if node not in visited:
                visited.add(node)
                queue.extend(graph.get(node, []))
    return None


def _check_iface(design: DesignResult, specs: SpecBundle) -> builtins.list[Finding]:
    if not design.component_changes and not design.architecture_decisions:
        return [_F("interface-coherence", "info", "No components or decisions — skipped")]
    iface = specs.interface_definitions.strip()
    if not iface:
        if design.component_changes:
            return [_F("interface-coherence", "warning",
                       "Component changes but no interface definitions")]
        return []
    findings: builtins.list[Finding] = []
    iface_types = _type_defs(iface)
    dtypes: set[str] = set()
    for m in re.finditer(r"\b([A-Z][a-z]+[A-Z]\w*)\b",
                         "\n".join(design.component_changes + design.api_changes)):
        if m.group(1) not in _SKIP:
            dtypes.add(m.group(1))
    if iface_types and dtypes:
        for t in sorted(dtypes - iface_types):
            findings.append(_F("interface-coherence", "warning",
                               f"Type '{t}' in design but not in interfaces"))
    dep_re = re.compile(
        r"([A-Za-z]\w+)\s*(?:->|→)\s*([A-Za-z]\w+)"
        r"|([A-Za-z]\w+)\s+(?:depends on|uses|imports)\s+([A-Za-z]\w+)", re.I,
    )
    edges: builtins.list[tuple[str, str]] = []
    for m in dep_re.finditer(iface):
        src, tgt = m.group(1) or m.group(3), m.group(2) or m.group(4)
        if src and tgt:
            edges.append((src, tgt))
    if edges:
        cycle = _detect_cycle(edges)
        if cycle:
            findings.append(_F("interface-coherence", "error",
                               f"Circular dependency: {cycle}"))
    return findings


# ── Check 4: Architecture guidance alignment ─────────────────────

def _check_arch(design: DesignResult, specs: SpecBundle) -> builtins.list[Finding]:
    if not design.architecture_decisions:
        return [_F("arch-guidance", "warning", "No architecture decisions in design")]
    guidance = specs.design_doc.strip()
    if not guidance:
        return [_F("arch-guidance", "info", "No design doc — cannot validate guidance")]
    recs = _RECOMMEND.findall(guidance)[:15]
    if not recs:
        return [_F("arch-guidance", "info", "No explicit recommendations in design doc")]
    findings: builtins.list[Finding] = []
    dec_text = "\n".join(design.architecture_decisions)
    for r in recs:
        if len(r) >= 3 and not re.search(re.escape(r), dec_text, re.I):  # noqa: PLR2004
            findings.append(_F("arch-guidance", "warning",
                               f"Guidance recommends '{r}' but not in decisions"))
    if design.component_changes and not design.risks:
        findings.append(_F("arch-guidance", "warning",
                           "Component changes but no risks documented"))
    return findings


# ── Public API ───────────────────────────────────────────────────


def run_design_review_gate(design: DesignResult, specs: SpecBundle) -> GateResult:
    """Run the design review validation gate.

    Validates: API consistency, data completeness, interface coherence,
    and architecture guidance alignment.
    """
    all_findings: builtins.list[Finding] = []
    all_findings.extend(_check_api(design, specs))
    all_findings.extend(_check_data(design, specs))
    all_findings.extend(_check_iface(design, specs))
    all_findings.extend(_check_arch(design, specs))
    blocking = tuple(f for f in all_findings if f.severity == "error")
    logger.info("Design review gate: %s (%d findings, %d blocking)",
                "PASSED" if blocking else "FAILED", len(all_findings), len(blocking))
    return GateResult(passed=len(blocking) == 0,
                      findings=tuple(all_findings), blocking_issues=blocking)
