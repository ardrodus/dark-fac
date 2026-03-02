"""Consolidated specification-validation gates.

Merges design review, contract validation, and integration test gate logic
into a single module, sharing helpers and constants through
:mod:`factory.gates.framework`.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path

from factory.gates.framework import (
    API_EXTS,
    IFACE_EXTS,
    SCHEMA_EXTS,
    GateReport,
    GateRunner,
    find_spec,
    find_typed_spec,
    read_file,
)
from factory.integrations.shell import run_command

logger = logging.getLogger(__name__)

# ── Shared regex patterns ────────────────────────────────────────

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
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)", re.I,
)
_LANG_MARKERS: dict[str, list[str]] = {
    "ts": ["tsconfig.json"], "go": ["go.mod"], "rs": ["Cargo.toml"],
    "py": ["pyproject.toml", "requirements.txt"],
    "java": ["pom.xml", "build.gradle"], "js": ["package.json"],
}
_API_EXT_MAP: dict[str, str] = {".yaml": "openapi", ".graphql": "graphql", ".proto": "grpc"}
_TYPE_CMDS: dict[str, list[str]] = {
    "ts": ["npx", "tsc", "--noEmit"], "py": ["mypy", "src/"],
    "go": ["go", "vet", "./..."], "rs": ["cargo", "check"],
}

# ── Shared helpers ───────────────────────────────────────────────


def _section(text: str, heading: str, limit: int = 300) -> str:
    """Extract a markdown section by heading."""
    m = re.search(rf"^## {heading}", text, re.I | re.M)
    if not m:
        return ""
    rest = text[m.end():]
    end = re.search(r"^## ", rest, re.M)
    return "\n".join((rest[:end.start()] if end else rest).splitlines()[:limit])


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


def _find_api_spec(sd: Path, issue: str) -> tuple[str, Path] | None:
    r = find_typed_spec(sd, "api-contract", issue, _API_EXT_MAP)
    if r:
        return r
    cli = sd / f"api-contract-{issue}-cli.yaml"
    return ("cli", cli) if cli.exists() else None


def _find_schema_spec(sd: Path, issue: str) -> tuple[str, Path] | None:
    return find_typed_spec(sd, "schema", issue, {".sql": "sql", ".json": "nosql"})


def _find_iface_spec(sd: Path, issue: str) -> tuple[str, Path] | None:
    return find_typed_spec(sd, "interfaces", issue, {f".{lang}": lang for lang in IFACE_EXTS})


def _detect_language(workspace: Path) -> str:
    for lang, markers in _LANG_MARKERS.items():
        if any((workspace / m).exists() for m in markers):
            return lang
    return "none"


def _grep_src(ws: Path, pattern: str) -> bool:
    src = ws / "src"
    return (not src.is_dir()) or run_command(
        ["grep", "-rqiE", pattern, str(src)], timeout=30,
    ).returncode == 0


def _yaml_paths(p: Path) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\s+(/.+?):", read_file(p), re.M)]


def _yaml_schemas(p: Path) -> list[str]:
    names: list[str] = []
    in_sec = False
    for line in read_file(p).splitlines():
        if re.match(r"^\s*schemas:", line):
            in_sec = True
        elif in_sec:
            if re.match(r"^[^ ]", line):
                break
            m = re.match(r"^\s+(\w+):$", line)
            if m:
                names.append(m.group(1))
    return names[:20]


def _extract_collections(data: object, out: list[str]) -> None:
    if isinstance(data, dict):
        if isinstance(data.get("collection_name"), str):
            out.append(data["collection_name"])
        for v in data.values():
            _extract_collections(v, out)
    elif isinstance(data, list):
        for item in data:
            _extract_collections(item, out)


# ═══════════════════════════════════════════════════════════════════
# Design Review checks
# ═══════════════════════════════════════════════════════════════════


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
    spec = find_spec(sd, "api-contract", issue, API_EXTS)
    if spec and endpoints:
        paths = set(re.findall(r"^\s+(/\S+?):", read_file(spec), re.M))
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
    spec = find_spec(sd, "schema", issue, SCHEMA_EXTS)
    if spec and schema_sec:
        dtables = set(_CREATE_TABLE_RE.findall(schema_sec))
        stables = set(_CREATE_TABLE_RE.findall(read_file(spec)))
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
    spec = find_spec(sd, "interfaces", issue, IFACE_EXTS)
    if spec and defs:
        spec_text = read_file(spec)
        missing = [t for t in sorted(defs) if t not in spec_text]
        if missing:
            raise ValueError(f"types in design but not spec: {', '.join(missing[:5])}")
    return "interfaces coherent"


def _check_arch_guidance(design: str, guidance_path: Path) -> bool | str:
    guidance = read_file(guidance_path)
    if not guidance:
        return "no architecture guidance — skipped"
    if not re.search(r"## Design Decisions", design, re.I):
        raise ValueError("design missing 'Design Decisions' section")
    recs = _RECOMMEND.findall(guidance)[:10]
    missing = [r for r in recs if len(r) >= 3 and not re.search(re.escape(r), design, re.I)]
    if missing:
        raise ValueError(f"guidance recommends but design omits: {', '.join(missing[:5])}")
    return "design aligns with guidance"


# ═══════════════════════════════════════════════════════════════════
# Contract Validation checks
# ═══════════════════════════════════════════════════════════════════


def _check_schema_validation(ws: Path, sd: Path, issue: str) -> bool | str:
    found = []
    for label, fn in (("api", _find_api_spec), ("schema", _find_schema_spec), ("iface", _find_iface_spec)):
        spec = fn(sd, issue)
        if spec:
            found.append(f"{label}({spec[0]})")
    return f"specs found: {', '.join(found)}" if found else "no specs found — skipped"


def _check_endpoint_coverage(ws: Path, sd: Path, issue: str) -> bool | str:
    api = _find_api_spec(sd, issue)
    if api is None:
        return "no API spec — skipped"
    api_type, contract = api
    missing: list[str] = []
    if api_type == "openapi":
        for p in _yaml_paths(contract):
            if not _grep_src(ws, p.replace("{", "[^/]*").replace("}", "")):
                missing.append(p)
        for name in _yaml_schemas(contract):
            pat = f"(class\\s+{name}|interface\\s+{name}|type\\s+{name}|struct\\s+{name})"
            if not _grep_src(ws, pat):
                missing.append(f"schema:{name}")
    elif api_type == "graphql":
        text = contract.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"type\s+(\w+)", text):
            t = m.group(1)
            if t not in ("Query", "Mutation", "Subscription") and not _grep_src(ws, t):
                missing.append(f"type:{t}")
    if missing:
        raise ValueError(f"missing in src: {', '.join(missing[:5])}")
    return f"all endpoints covered ({api_type})"


def _check_backward_compat(ws: Path, sd: Path, issue: str) -> bool | str:
    schema = _find_schema_spec(sd, issue)
    if schema is None:
        return "no schema spec — skipped"
    kind, path = schema
    if kind == "sql":
        text = read_file(path)
        tables = _CREATE_TABLE_RE.findall(text)
        missing = []
        for t in dict.fromkeys(tables):
            pascal = re.sub(r"(?:^|_)([a-z])", lambda m: m.group(1).upper(), t)
            if not _grep_src(ws, f"({pascal}|{t})"):
                missing.append(t)
        if missing:
            raise ValueError(f"tables missing in src: {', '.join(missing[:5])}")
        return f"all {len(tables)} table(s) found in source"
    if kind == "nosql":
        try:
            data = json.loads(read_file(path))
        except json.JSONDecodeError:
            return "nosql schema parsed (non-JSON)"
        colls: list[str] = []
        _extract_collections(data, colls)
        bad = [c for c in colls if not _grep_src(ws, c)]
        if bad:
            raise ValueError(f"collections missing: {', '.join(bad[:5])}")
        return f"all {len(colls)} collection(s) found"
    return "unknown schema type"


def _check_type_consistency(ws: Path, sd: Path, issue: str) -> bool | str:
    iface = _find_iface_spec(sd, issue)
    lang = iface[0] if iface else _detect_language(ws)
    if lang == "none":
        return "no language detected — skipped"
    cmd = _TYPE_CMDS.get(lang)
    if cmd is None:
        return f"no type-check tool for {lang} — skipped"
    result = run_command(cmd, timeout=120, cwd=str(ws))
    if result.returncode == 0:
        return f"type check passed ({lang})"
    summary = (result.stderr or result.stdout).strip().splitlines()[:3]
    raise ValueError(f"type check failed ({lang}): {'; '.join(summary)}")


# ═══════════════════════════════════════════════════════════════════
# Integration Test checks
# ═══════════════════════════════════════════════════════════════════


def collect_story_artifacts(specs_dir: Path, story_ids: list[str]) -> str:
    """Gather design docs, contracts, schemas, interfaces, test strategies."""
    sections: list[str] = []
    for sid in story_ids:
        parts: list[str] = []
        design = specs_dir / f"design-{sid}.md"
        if design.is_file():
            parts.append(f"#### Design (Story {sid})\n{read_file(design)}")
        contract = find_spec(specs_dir, "api-contract", sid, API_EXTS)
        if contract:
            parts.append(f"#### API Contract (Story {sid})\n```\n{read_file(contract)}\n```")
        schema = find_spec(specs_dir, "schema", sid, SCHEMA_EXTS)
        if schema:
            parts.append(f"#### Schema (Story {sid})\n```\n{read_file(schema)}\n```")
        iface = find_spec(specs_dir, "interfaces", sid, IFACE_EXTS)
        if iface:
            parts.append(f"#### Interfaces (Story {sid})\n```\n{read_file(iface)}\n```")
        strategy = specs_dir / f"test-strategy-{sid}.md"
        if strategy.is_file():
            parts.append(f"#### Test Strategy (Story {sid})\n{read_file(strategy)}")
        if parts:
            sections.append(f"### Story {sid}\n" + "\n".join(parts))
    return "\n\n".join(sections)


def collect_existing_tests(workspace: Path) -> list[str]:
    """Return relative paths of existing test files."""
    tests_dir = workspace / "tests"
    if not tests_dir.is_dir():
        return []
    return [
        str(p.relative_to(workspace))
        for p in sorted(tests_dir.rglob("*"))
        if p.is_file() and p.suffix in {".ts", ".js", ".py", ".go", ".rs", ".java", ".rb", ".sh"}
    ]


def _check_artifacts_present(sd: Path, story_ids: list[str]) -> bool | str:
    found: list[str] = []
    for sid in story_ids:
        if (sd / f"design-{sid}.md").is_file():
            found.append(f"design-{sid}")
        if find_spec(sd, "api-contract", sid, API_EXTS):
            found.append(f"contract-{sid}")
    if not found:
        return "no story artifacts found — integration skipped"
    return f"artifacts found: {', '.join(found)}"


def _check_test_dir_exists(workspace: Path) -> bool | str:
    tests_dir = workspace / "tests"
    if not tests_dir.is_dir():
        return "no tests/ directory — integration skipped"
    return f"tests/ exists ({len(collect_existing_tests(workspace))} test file(s))"


def _check_integration_dir(workspace: Path) -> bool | str:
    integ_dir = workspace / "tests" / "integration"
    if not integ_dir.is_dir():
        return "no tests/integration/ — will be created by agent"
    count = sum(1 for f in integ_dir.iterdir() if f.is_file())
    return f"tests/integration/ has {count} file(s)"


def _check_cross_story_boundaries(sd: Path, story_ids: list[str]) -> bool | str:
    if len(story_ids) < 2:
        return "single story — no cross-story boundaries to check"
    shared_entities: dict[str, list[str]] = {}
    for sid in story_ids:
        design = read_file(sd / f"design-{sid}.md")
        for m in re.finditer(
            r"(?:class|interface|type|struct)\s+([A-Z][A-Za-z0-9]+)", design,
        ):
            shared_entities.setdefault(m.group(1), []).append(sid)
    shared = {e: sids for e, sids in shared_entities.items() if len(sids) > 1}
    if shared:
        names = ", ".join(f"{e}({'/'.join(s)})" for e, s in list(shared.items())[:5])
        return f"shared entities across stories: {names}"
    return "no shared entities detected between stories"


# ═══════════════════════════════════════════════════════════════════
# Runner factories & public API
# ═══════════════════════════════════════════════════════════════════


def _register_design_checks(
    runner: GateRunner, design: str, sd: Path, iid: str, gp: Path,
) -> None:
    if not design:
        runner.register_check("design-file", lambda: "no design file — skipped")
    else:
        runner.register_check("api-consistency", lambda: _check_api_consistency(design, sd, iid))
        runner.register_check("data-completeness", lambda: _check_data_completeness(design, sd, iid))
        runner.register_check("interface-coherence", lambda: _check_interface_coherence(design, sd, iid))
        runner.register_check("arch-guidance", lambda: _check_arch_guidance(design, gp))


def create_design_review_runner(
    workspace: str | Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Create a configured design-review gate runner."""
    ws = Path(workspace)
    sd = ws / ".dark-factory" / "specs"
    design_files = list(sd.glob("design-*.md")) if sd.is_dir() else []
    design = read_file(design_files[0]) if design_files else ""
    m = re.search(r"(\d+)", design_files[0].stem) if design_files else None
    runner = GateRunner("design-review", metrics_dir=metrics_dir)
    _register_design_checks(runner, design, sd, m.group(1) if m else "0", ws / ".dark-factory" / "architecture-guidance.md")
    return runner


def run_design_review(
    design_file: str | Path, specs_dir: str | Path, guidance_file: str | Path,
    *, metrics_dir: str | Path | None = None,
) -> GateReport:
    """Run the full design review gate with four checks."""
    dp, sd, gp = Path(design_file), Path(specs_dir), Path(guidance_file)
    m = re.search(r"(\d+)", dp.stem)
    runner = GateRunner("design-review", metrics_dir=metrics_dir)
    _register_design_checks(runner, read_file(dp), sd, m.group(1) if m else "0", gp)
    return runner.run()


def _register_contract_checks(
    runner: GateRunner, ws: Path, sd: Path, issue: str,
) -> None:
    runner.register_check("schema-validation", lambda: _check_schema_validation(ws, sd, issue))
    runner.register_check("endpoint-coverage", lambda: _check_endpoint_coverage(ws, sd, issue))
    runner.register_check("backward-compatibility", lambda: _check_backward_compat(ws, sd, issue))
    runner.register_check("type-consistency", lambda: _check_type_consistency(ws, sd, issue))


def create_contract_validation_runner(
    workspace: str | Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Create a configured contract-validation gate runner."""
    ws = Path(workspace)
    runner = GateRunner("contract-validation", metrics_dir=metrics_dir)
    _register_contract_checks(runner, ws, ws / ".dark-factory" / "specs", "0")
    return runner


def run_contract_validation(
    workspace: str | Path, specs_dir: str | Path, issue: str,
    *, metrics_dir: str | Path | None = None,
) -> GateReport:
    """Run the full contract validation gate."""
    runner = GateRunner("contract-validation", metrics_dir=metrics_dir)
    _register_contract_checks(runner, Path(workspace), Path(specs_dir), issue)
    return runner.run()


def _register_integration_checks(
    runner: GateRunner, ws: Path, sd: Path, story_ids: list[str],
) -> None:
    runner.register_check("artifacts-present", lambda: _check_artifacts_present(sd, story_ids))
    runner.register_check("test-dir", lambda: _check_test_dir_exists(ws))
    runner.register_check("integration-dir", lambda: _check_integration_dir(ws))
    runner.register_check("cross-story-boundaries", lambda: _check_cross_story_boundaries(sd, story_ids))


def create_integration_test_runner(
    workspace: str | Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Create a configured integration-test gate runner."""
    ws = Path(workspace)
    runner = GateRunner("integration-test", metrics_dir=metrics_dir)
    _register_integration_checks(runner, ws, ws / ".dark-factory" / "specs", [])
    return runner


def run_integration_test_gate(
    workspace: str | Path, specs_dir: str | Path, story_ids: list[str],
    *, metrics_dir: str | Path | None = None,
) -> GateReport:
    """Run the integration test gate."""
    runner = GateRunner("integration-test", metrics_dir=metrics_dir)
    _register_integration_checks(runner, Path(workspace), Path(specs_dir), story_ids)
    return runner.run()
