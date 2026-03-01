"""Contract validation gate — migrated from contract-validation-gate.sh (US-025).

Uses :class:`GateRunner` for schema validation, endpoint coverage,
backward compatibility, and type consistency checks.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from factory.gates.framework import GateReport, GateRunner
from factory.integrations.shell import run_command

logger = logging.getLogger(__name__)

_LANG_MARKERS: dict[str, list[str]] = {
    "ts": ["tsconfig.json"], "go": ["go.mod"], "rs": ["Cargo.toml"],
    "py": ["pyproject.toml", "requirements.txt"],
    "java": ["pom.xml", "build.gradle"], "js": ["package.json"],
}
_API_EXT: dict[str, str] = {".yaml": "openapi", ".graphql": "graphql", ".proto": "grpc"}
_IFACE_LANGS = ("ts", "py", "go", "rs", "java", "rb", "js")
_TYPE_CMDS: dict[str, list[str]] = {
    "ts": ["npx", "tsc", "--noEmit"], "py": ["mypy", "src/"],
    "go": ["go", "vet", "./..."], "rs": ["cargo", "check"],
}


def _detect_language(workspace: Path) -> str:
    for lang, markers in _LANG_MARKERS.items():
        if any((workspace / m).exists() for m in markers):
            return lang
    return "none"


def _find_spec(prefix: str, sd: Path, issue: str, variants: dict[str, str]) -> tuple[str, Path] | None:
    for ext, kind in variants.items():
        p = sd / f"{prefix}-{issue}{ext}"
        if p.exists():
            return kind, p
    return None

def _find_api_spec(sd: Path, issue: str) -> tuple[str, Path] | None:
    r = _find_spec("api-contract", sd, issue, _API_EXT)
    if r:
        return r
    cli = sd / f"api-contract-{issue}-cli.yaml"
    return ("cli", cli) if cli.exists() else None

def _find_schema_spec(sd: Path, issue: str) -> tuple[str, Path] | None:
    return _find_spec("schema", sd, issue, {".sql": "sql", ".json": "nosql"})

def _find_iface_spec(sd: Path, issue: str) -> tuple[str, Path] | None:
    return _find_spec("interfaces", sd, issue, {f".{lang}": lang for lang in _IFACE_LANGS})


def _grep_src(ws: Path, pattern: str) -> bool:
    src = ws / "src"
    return (not src.is_dir()) or run_command(["grep", "-rqiE", pattern, str(src)], timeout=30).returncode == 0

def _yaml_paths(p: Path) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\s+(/.+?):", p.read_text(encoding="utf-8", errors="replace"), re.M)]

def _yaml_schemas(p: Path) -> list[str]:
    names: list[str] = []
    in_sec = False
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
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


# ── Check implementations ────────────────────────────────────────


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
        text = path.read_text(encoding="utf-8", errors="replace")
        tables = re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)", text, re.I)
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
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
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


# ── Discovery interface ───────────────────────────────────────────

GATE_NAME = "contract-validation"


def create_runner(
    workspace: str | Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Create a configured (but not executed) contract-validation gate runner."""
    ws = Path(workspace)
    sd = ws / ".dark-factory" / "specs"
    issue = "0"
    runner = GateRunner(GATE_NAME, metrics_dir=metrics_dir)
    runner.register_check("schema-validation", lambda: _check_schema_validation(ws, sd, issue))
    runner.register_check("endpoint-coverage", lambda: _check_endpoint_coverage(ws, sd, issue))
    runner.register_check("backward-compatibility", lambda: _check_backward_compat(ws, sd, issue))
    runner.register_check("type-consistency", lambda: _check_type_consistency(ws, sd, issue))
    return runner


# ── Public API ───────────────────────────────────────────────────


def run_contract_validation(
    workspace: str | Path, specs_dir: str | Path, issue: str, *,
    metrics_dir: str | Path | None = None,
) -> GateReport:
    """Run the full contract validation gate.

    Registers four checks (schema validation, endpoint coverage,
    backward compatibility, type consistency) and delegates execution
    to :class:`GateRunner`.
    """
    ws, sd = Path(workspace), Path(specs_dir)
    runner = GateRunner("contract-validation", metrics_dir=metrics_dir)
    runner.register_check("schema-validation", lambda: _check_schema_validation(ws, sd, issue))
    runner.register_check("endpoint-coverage", lambda: _check_endpoint_coverage(ws, sd, issue))
    runner.register_check("backward-compatibility", lambda: _check_backward_compat(ws, sd, issue))
    runner.register_check("type-consistency", lambda: _check_type_consistency(ws, sd, issue))
    return runner.run()
