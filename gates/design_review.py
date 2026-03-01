"""Design review gate — migrated from design-review-gate.sh (US-013/US-026).

Validates a design document across four dimensions: API consistency,
data model completeness, interface coherence, and architecture guidance.
Uses :class:`GateRunner` from :mod:`factory.gates.framework`.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path

from factory.gates.framework import GateReport, GateRunner

logger = logging.getLogger(__name__)

_HTTP_METHODS = re.compile(r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S+)", re.I)
_TYPE_DEF = re.compile(
    r"(?:export\s+)?(?:interface|type|class|struct|trait|enum)\s+([A-Z][A-Za-z0-9]+)",
)
_SCHEMA_REF = re.compile(
    r"(?:CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?)"
    r"|(?:(?:class|interface|type|struct|entity|model|table)\s+([A-Z][A-Za-z0-9]+))",
    re.I,
)
_TYPE_REF = re.compile(
    r"(?::\s*|->?\s*|returns?\s+)([A-Z][A-Za-z0-9]+)"
    r"|([A-Z][A-Za-z0-9]+)\[\]"
    r"|(?:Promise|List|Optional|Result)<([A-Z][A-Za-z0-9]+)",
)
_MODULE_DEP = re.compile(
    r"([A-Za-z]\w+)\s*(?:->|→)\s*([A-Za-z]\w+)"
    r"|([A-Za-z]\w+)\s+(?:depends on|uses|imports|requires)\s+([A-Za-z]\w+)", re.I,
)
_RECOMMEND = re.compile(
    r"(?:recommend|should use|must use|prefer|advised)\s+([A-Za-z0-9_-]+)", re.I,
)
_SKIP_WORDS = frozenset([
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "HTTP", "HTTPS",
    "API", "REST", "JSON", "XML", "YAML", "OK", "NOT", "NULL", "TRUE", "FALSE",
    "UUID", "INT", "STRING", "BOOLEAN", "String", "Int", "Integer", "Float",
    "Double", "Boolean", "Bool", "Void", "Error", "Promise", "Result", "Optional",
    "List", "Array", "Map", "Set", "Date", "DateTime", "Null",
])
_API_EXTS = ("yaml", "graphql", "proto")
_IFACE_EXTS = ("ts", "py", "go", "rs", "java", "rb", "js")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def _section(text: str, heading: str, limit: int = 300) -> str:
    m = re.search(rf"^## {heading}", text, re.I | re.M)
    if not m:
        return ""
    rest = text[m.end():]
    end = re.search(r"^## ", rest, re.M)
    return "\n".join((rest[:end.start()] if end else rest).splitlines()[:limit])


def _find_spec(sd: Path, prefix: str, issue: str, exts: tuple[str, ...]) -> Path | None:
    for ext in exts:
        p = sd / f"{prefix}-{issue}.{ext}"
        if p.is_file():
            return p
    return None


def _detect_cycles(edges: list[tuple[str, str]]) -> str | None:
    graph: dict[str, list[str]] = defaultdict(list)
    for src, tgt in edges:
        graph[src].append(tgt)
    for start in graph:
        visited: set[str] = set()
        queue = list(graph[start])
        while queue:
            node = queue.pop(0)
            if node == start:
                return f"{start} -> ... -> {start}"
            if node not in visited:
                visited.add(node)
                queue.extend(graph.get(node, []))
    return None


def _all_defs(text: str) -> set[str]:
    defs = {m.group(1) for m in _TYPE_DEF.finditer(text)}
    defs |= {m.group(1) or m.group(2) for m in _SCHEMA_REF.finditer(text)}
    defs.discard(None)
    return defs


# ── Check implementations ────────────────────────────────────────


def _check_api_consistency(design: str, sd: Path, issue: str) -> bool | str:
    if not re.search(r"(## API|endpoint|route|REST|GraphQL|gRPC|/api/)", design, re.I):
        return "no API content — skipped"
    endpoints = _HTTP_METHODS.findall(design)
    seen: set[str] = set()
    dupes = []
    for method, path in endpoints:
        key = f"{method.upper()} {path}"
        if key in seen:
            dupes.append(key)
        seen.add(key)
    if dupes:
        raise ValueError(f"duplicate endpoints: {', '.join(dupes[:5])}")
    api_sec = _section(design, "API")
    if api_sec:
        defs = _all_defs(design)
        orphaned = [
            m.group(1) for m in re.finditer(r"\b([A-Z][a-z]+[A-Z]\w*)\b", api_sec)
            if m.group(1) not in _SKIP_WORDS and m.group(1) not in defs
        ]
        if orphaned:
            raise ValueError(f"types in API not defined: {', '.join(set(orphaned[:5]))}")
    spec = _find_spec(sd, "api-contract", issue, _API_EXTS)
    if spec and endpoints:
        paths = set(re.findall(r"^\s+(/\S+?):", _read(spec), re.M))
        missing = [f"{m} {p}" for m, p in endpoints if paths and p not in paths]
        if missing:
            raise ValueError(f"endpoints not in contract: {', '.join(missing[:5])}")
    return "API consistency verified"


def _check_data_completeness(design: str, sd: Path, issue: str) -> bool | str:
    if not re.search(
        r"(## Database|## Data Model|## Schema|CREATE TABLE|entity|collection)", design, re.I,
    ):
        return "no data model content — skipped"
    schema_sec = _section(design, "Database") or _section(design, "Data Model") or _section(design, "Schema")
    if schema_sec:
        defined = _all_defs(schema_sec)
        fk_refs = re.findall(r"REFERENCES\s+[`\"]?(\w+)[`\"]?", schema_sec, re.I)
        bad = [fk for fk in dict.fromkeys(fk_refs) if not any(fk.lower() == d.lower() for d in defined)]
        if bad:
            raise ValueError(f"FK to undefined tables: {', '.join(bad[:5])}")
    spec = _find_spec(sd, "schema", issue, ("sql", "json"))
    if spec and schema_sec:
        dtables = set(re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)", schema_sec, re.I))
        stables = set(re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)", _read(spec), re.I))
        if dtables and stables:
            diff = {t for t in dtables if not any(t.lower() == s.lower() for s in stables)}
            if diff:
                raise ValueError(f"tables in design but not spec: {', '.join(diff)}")
    return "data model complete"


def _check_interface_coherence(design: str, sd: Path, issue: str) -> bool | str:
    if not re.search(
        r"(## Module|## Interface|## Component|## Dependency|import|requires|depends)", design, re.I,
    ):
        return "no interface content — skipped"
    edges: list[tuple[str, str]] = []
    for m in _MODULE_DEP.finditer(design):
        src, tgt = m.group(1) or m.group(3), m.group(2) or m.group(4)
        if src and tgt:
            edges.append((src, tgt))
    if edges:
        cycle = _detect_cycles(edges)
        if cycle:
            raise ValueError(f"circular dependency: {cycle}")
    defs = _all_defs(design)
    refs = set()
    for m in _TYPE_REF.finditer(design):
        ref = m.group(1) or m.group(2) or m.group(3)
        if ref and ref not in _SKIP_WORDS:
            refs.add(ref)
    undefined = refs - defs
    if undefined:
        raise ValueError(f"types referenced but not defined: {', '.join(sorted(undefined)[:5])}")
    spec = _find_spec(sd, "interfaces", issue, _IFACE_EXTS)
    if spec and defs:
        spec_text = _read(spec)
        missing = [t for t in sorted(defs) if t not in spec_text]
        if missing:
            raise ValueError(f"types in design but not spec: {', '.join(missing[:5])}")
    return "interfaces coherent"


def _check_arch_guidance(design: str, guidance_path: Path) -> bool | str:
    guidance = _read(guidance_path)
    if not guidance:
        return "no architecture guidance — skipped"
    if not re.search(r"## Design Decisions", design, re.I):
        raise ValueError("design missing 'Design Decisions' section")
    recs = _RECOMMEND.findall(guidance)[:10]
    missing = [r for r in recs if len(r) >= 3 and not re.search(re.escape(r), design, re.I)]
    if missing:
        raise ValueError(f"guidance recommends but design omits: {', '.join(missing[:5])}")
    return "design aligns with guidance"


# ── Discovery interface ───────────────────────────────────────────

GATE_NAME = "design-review"


def create_runner(
    workspace: str | Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Create a configured (but not executed) design-review gate runner."""
    ws = Path(workspace)
    sd = ws / ".dark-factory" / "specs"
    gp = ws / ".dark-factory" / "architecture-guidance.md"
    design_files = list(sd.glob("design-*.md")) if sd.is_dir() else []
    design = _read(design_files[0]) if design_files else ""
    m = re.search(r"(\d+)", design_files[0].stem) if design_files else None
    iid = m.group(1) if m else "0"
    runner = GateRunner(GATE_NAME, metrics_dir=metrics_dir)
    if not design:
        runner.register_check("design-file", lambda: "no design file — skipped")
    else:
        runner.register_check("api-consistency", lambda: _check_api_consistency(design, sd, iid))
        runner.register_check("data-completeness", lambda: _check_data_completeness(design, sd, iid))
        runner.register_check("interface-coherence", lambda: _check_interface_coherence(design, sd, iid))
        runner.register_check("arch-guidance", lambda: _check_arch_guidance(design, gp))
    return runner


# ── Public API ───────────────────────────────────────────────────


def run_design_review(
    design_file: str | Path, specs_dir: str | Path, guidance_file: str | Path,
    *, metrics_dir: str | Path | None = None,
) -> GateReport:
    """Run the full design review gate with four checks."""
    dp, sd, gp = Path(design_file), Path(specs_dir), Path(guidance_file)
    m = re.search(r"(\d+)", dp.stem)
    iid = m.group(1) if m else "0"
    design = _read(dp)
    runner = GateRunner("design-review", metrics_dir=metrics_dir)
    if not design:
        runner.register_check("design-file", lambda: "no design file — skipped")
        return runner.run()
    runner.register_check("api-consistency", lambda: _check_api_consistency(design, sd, iid))
    runner.register_check("data-completeness", lambda: _check_data_completeness(design, sd, iid))
    runner.register_check("interface-coherence", lambda: _check_interface_coherence(design, sd, iid))
    runner.register_check("arch-guidance", lambda: _check_arch_guidance(design, gp))
    return runner.run()
