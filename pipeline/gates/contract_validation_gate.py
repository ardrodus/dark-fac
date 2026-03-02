"""Contract validation gate — validates implementation conforms to specs.

Port of contract-validation-gate.sh.  Validates API contract conformance,
schema/DDL implementation, and interface definitions after engineering
completes.  Spec violations block PR creation and return structured
mismatch reports.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import builtins

from factory.pipeline.tdd.test_writer import SpecBundle
from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)

_SRC_EXTS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".cs",
})

_OPENAPI_PATH = re.compile(r"^\s+(/[a-zA-Z0-9_/{}\-]+)\s*:", re.M)
_GRAPHQL_TYPE = re.compile(r"\btype\s+(\w+)\b")
_GRPC_SERVICE = re.compile(r"\bservice\s+(\w+)\b")
_GRPC_RPC = re.compile(r"\brpc\s+(\w+)\b")
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)", re.I,
)
_TYPE_DEF = re.compile(
    r"(?:export\s+)?(?:interface|type|class|struct|trait|enum|Protocol|TypedDict)"
    r"\s+([A-Z][A-Za-z0-9_]+)",
)
_SKIP_GQL = frozenset({"Query", "Mutation", "Subscription"})
_SKIP_GENERIC = frozenset({
    "String", "Int", "Integer", "Float", "Double", "Boolean", "Bool",
    "Void", "Error", "Promise", "Result", "Optional", "List", "Array",
    "Map", "Set", "Date", "DateTime", "Object", "Any",
})


@dataclass(frozen=True, slots=True)
class Violation:
    """Single contract violation with location and mismatch detail."""

    dimension: str  # "api_contract" | "schema" | "interface"
    file: str
    line: int
    expected: str
    actual: str


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of the contract validation gate."""

    passed: bool
    violations: tuple[Violation, ...] = field(default_factory=tuple)


def _V(  # noqa: N802
    dim: str, expected: str, actual: str = "", *, file: str = "", line: int = 0,
) -> Violation:
    return Violation(dimension=dim, file=file, line=line, expected=expected, actual=actual)


def _load_sources(ws_path: str) -> builtins.list[tuple[Path, str]]:
    """Load source files from workspace.  Prefers ``src/`` subdirectory."""
    root = Path(ws_path)
    search = root / "src" if (root / "src").is_dir() else root
    out: builtins.list[tuple[Path, str]] = []
    for fp in search.rglob("*"):
        if fp.suffix in _SRC_EXTS and fp.is_file():
            try:
                out.append((fp, fp.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return out


def _grep(sources: builtins.list[tuple[Path, str]], pattern: str) -> bool:
    """Return ``True`` if *pattern* matches any source file content."""
    pat = re.compile(pattern, re.I)
    return any(pat.search(content) for _, content in sources)


# ── Check 1: API contract ────────────────────────────────────────


def _check_api(
    specs: SpecBundle, sources: builtins.list[tuple[Path, str]],
) -> builtins.list[Violation]:
    contract = specs.api_contract.strip()
    if not contract:
        return []
    violations: builtins.list[Violation] = []

    is_graphql = bool(re.search(r"\btype\s+Query\b", contract))
    is_grpc = bool(
        re.search(r"\bsyntax\s*=", contract) and re.search(r"\bservice\s", contract),
    )

    if is_graphql:
        for m in _GRAPHQL_TYPE.finditer(contract):
            name = m.group(1)
            if name in _SKIP_GQL or name in _SKIP_GENERIC:
                continue
            if not _grep(sources, rf"\b{re.escape(name)}\b"):
                violations.append(_V("api_contract",
                                     f"GraphQL type '{name}' implemented", "not found"))
    elif is_grpc:
        for m in _GRPC_SERVICE.finditer(contract):
            if not _grep(sources, rf"\b{re.escape(m.group(1))}\b"):
                violations.append(_V("api_contract",
                                     f"gRPC service '{m.group(1)}' implemented", "not found"))
        for m in _GRPC_RPC.finditer(contract):
            if not _grep(sources, rf"\b{re.escape(m.group(1))}\b"):
                violations.append(_V("api_contract",
                                     f"gRPC RPC '{m.group(1)}' implemented", "not found"))
    else:
        # OpenAPI / REST — verify paths exist as route registrations
        paths: set[str] = set()
        for m in _OPENAPI_PATH.finditer(contract):
            paths.add(m.group(1))
        for path in sorted(paths):
            escaped = re.escape(path)
            escaped = re.sub(r"\\{[^}]+\\}", r"[^/]+", escaped)
            colon = re.sub(r"\{([^}]+)\}", r":\1", path)
            if not _grep(sources, f"({escaped}|{re.escape(colon)})"):
                violations.append(_V("api_contract",
                                     f"Route '{path}' implemented", "not found"))
        # Check schema component types
        schema_sec = re.search(r"schemas:\s*\n((?:\s+\w+:.*\n?)+)", contract)
        if schema_sec:
            for m in re.finditer(r"^\s{4}(\w+):", schema_sec.group(1), re.M):
                name = m.group(1)
                if name in _SKIP_GENERIC:
                    continue
                pat = (
                    rf"(class|interface|type|struct)\s+{re.escape(name)}"
                    rf"|{re.escape(name)}(Schema|Model|DTO)"
                )
                if not _grep(sources, pat):
                    violations.append(_V("api_contract",
                                         f"Schema type '{name}' implemented", "not found"))
    return violations


# ── Check 2: Schema / DDL ────────────────────────────────────────


def _check_schema(
    specs: SpecBundle, sources: builtins.list[tuple[Path, str]],
) -> builtins.list[Violation]:
    schema = specs.schema_spec.strip()
    if not schema:
        return []
    violations: builtins.list[Violation] = []
    tables = _CREATE_TABLE.findall(schema)
    for table in tables:
        pascal = "".join(w.capitalize() for w in table.split("_"))
        singular = pascal.rstrip("s") if pascal.endswith("s") and len(pascal) > 2 else pascal  # noqa: PLR2004
        pat = (
            rf"(class|model|entity|struct)\s+({re.escape(pascal)}|{re.escape(singular)})"
            rf"|['\"]({re.escape(table)})['\"]"
            rf"|table.*{re.escape(table)}"
        )
        if not _grep(sources, pat):
            violations.append(_V("schema",
                                 f"Table '{table}' has model in source", "not found"))
    return violations


# ── Check 3: Interface conformance ───────────────────────────────


def _check_interfaces(
    specs: SpecBundle, sources: builtins.list[tuple[Path, str]],
) -> builtins.list[Violation]:
    iface = specs.interface_definitions.strip()
    if not iface:
        return []
    violations: builtins.list[Violation] = []
    for m in _TYPE_DEF.finditer(iface):
        name = m.group(1)
        if name in _SKIP_GENERIC:
            continue
        if not _grep(sources, rf"\b{re.escape(name)}\b"):
            violations.append(_V("interface",
                                 f"Type '{name}' from interface spec implemented", "not found"))
    return violations


# ── Public API ───────────────────────────────────────────────────


def run_contract_validation(workspace: Workspace, specs: SpecBundle) -> GateResult:
    """Run the contract validation gate.

    Validates three dimensions after engineering completes:

    1. **API contract** — endpoints / types from spec exist in implementation.
    2. **Schema** — tables from DDL spec have corresponding models.
    3. **Interfaces** — declared types are implemented in source.
    """
    sources = _load_sources(workspace.path)
    all_violations: builtins.list[Violation] = []
    all_violations.extend(_check_api(specs, sources))
    all_violations.extend(_check_schema(specs, sources))
    all_violations.extend(_check_interfaces(specs, sources))
    passed = len(all_violations) == 0
    logger.info(
        "Contract validation gate: %s (%d violations)",
        "PASSED" if passed else "FAILED",
        len(all_violations),
    )
    return GateResult(passed=passed, violations=tuple(all_violations))
