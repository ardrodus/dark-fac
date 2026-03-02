"""API contract generator — port of generate-api-contract.sh."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.setup.project_analyzer import AnalysisResult

from dark_factory.specs.base import (
    run_generate,
    save_artifact,
    strip_fences,
    validate_checks,
)
from dark_factory.specs.design_generator import DesignResult

_SKIP = frozenset(("node_modules", ".git", "vendor", "__pycache__"))
_PY = ("requirements.txt", "setup.py", "setup.cfg", "pyproject.toml")
_GQL_JS = ("apollo-server", "@apollo/server", "graphql-yoga", "express-graphql", "@nestjs/graphql", "type-graphql")
_GQL_PY = ("graphene", "strawberry", "ariadne", "graphql-core")
_CLI_JS = ("commander", "yargs", "oclif", "inquirer", "meow", "cac", "citty", "clipanion")
_CLI_PY = ("click", "typer", "argparse", "fire", "cement")
_CLI_RS = ("clap", "structopt")
_CLI_GO = ("cobra", "urfave/cli", "kong")
_REST_JS = (
    "express", "fastify", "koa", "hapi", "@hapi/hapi", "@nestjs/core", "next", "nuxt", "hono", "elysia"
)
_REST_PY = ("flask", "fastapi", "django", "djangorestframework", "starlette", "tornado", "falcon", "sanic", "aiohttp")
_REST_JVM = ("spring-boot", "spring-web", "javax.ws.rs", "jakarta.ws.rs", "quarkus", "micronaut", "dropwizard")
_REST_GO = ("gin-gonic", "gorilla/mux", "chi", "echo", "fiber")
_REST_RS = ("actix-web", "axum", "rocket", "warp", "tide")
_REST_CS = ("Microsoft.AspNetCore", "Swashbuckle", "Microsoft.NET.Sdk.Web")


class ContractType(Enum):
    OPENAPI = "openapi"
    GRAPHQL = "graphql"
    GRPC = "grpc"
    CLI = "cli"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ContractResult:
    """Structured output of API contract generation."""
    contract_type: ContractType
    spec_content: str
    validation_passed: bool
    validation_messages: tuple[str, ...] = field(default_factory=tuple)
    output_path: str = ""
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


_META: dict[ContractType, tuple[str, str]] = {
    ContractType.OPENAPI: ("OpenAPI 3.1 YAML", ".yaml"),
    ContractType.GRAPHQL: ("GraphQL SDL", ".graphql"),
    ContractType.GRPC: ("Protocol Buffers (proto3)", ".proto"),
    ContractType.CLI: ("CLI Command Specification YAML", "-cli.yaml"),
}
_CHECKS: dict[ContractType, list[tuple[str, str]]] = {
    ContractType.OPENAPI: [
        (r"(?m)^openapi:\s", "Missing 'openapi:' version"),
        (r"(?m)^info:", "Missing 'info:' section"),
        (r"(?m)^paths:", "Missing 'paths:' section")],
    ContractType.GRAPHQL: [(r"(?m)^\s*type\s+\w+", "No type definitions found")],
    ContractType.GRPC: [
        (r"(?m)^\s*syntax\s*=", "Missing 'syntax' declaration"),
        (r"(?m)^\s*service\s+\w+", "No service definitions"),
        (r"(?m)^\s*message\s+\w+", "No message definitions")],
    ContractType.CLI: [(r"(?mi)^\s*(?:name|command):", "Missing 'name:' or 'command:'")],
}


def _rd(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _kw(text: str, kws: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in kws)


def _detect(ws: Path) -> ContractType:  # noqa: PLR0911
    if not ws.is_dir():
        return ContractType.NONE
    ok = lambda f: not any(p in _SKIP for p in f.parts)  # noqa: E731
    if any(ok(f) for f in ws.rglob("*.proto")):
        return ContractType.GRPC
    if any(f.suffix in (".graphql", ".gql", ".graphqls") for f in ws.rglob("*") if f.is_file() and ok(f)):
        return ContractType.GRAPHQL
    pkg = _rd(ws / "package.json")
    if _kw(pkg, _GQL_JS):
        return ContractType.GRAPHQL
    for rf in _PY:
        if _kw(_rd(ws / rf), _GQL_PY):
            return ContractType.GRAPHQL
    if _kw(pkg, _CLI_JS) or '"bin"' in pkg:
        return ContractType.CLI
    for rf in _PY:
        if _kw(_rd(ws / rf), _CLI_PY):
            return ContractType.CLI
    if _kw(_rd(ws / "Cargo.toml"), _CLI_RS) or _kw(_rd(ws / "go.mod"), _CLI_GO):
        return ContractType.CLI
    if _kw(pkg, _REST_JS):
        return ContractType.OPENAPI
    for rf in _PY:
        if _kw(_rd(ws / rf), _REST_PY):
            return ContractType.OPENAPI
    for bf in ("pom.xml", "build.gradle", "build.gradle.kts"):
        if _kw(_rd(ws / bf), _REST_JVM):
            return ContractType.OPENAPI
    if _kw(_rd(ws / "go.mod"), _REST_GO) or _kw(_rd(ws / "Cargo.toml"), _REST_RS):
        return ContractType.OPENAPI
    for cs in ws.glob("**/*.csproj"):
        if ok(cs) and _kw(_rd(cs), _REST_CS):
            return ContractType.OPENAPI
    return ContractType.NONE


def _build_prompt(design: DesignResult, ct: ContractType, issue: int | str) -> str:
    fmt = _META.get(ct, ("OpenAPI 3.1 YAML", ""))[0]
    parts = (["## Architecture"] + [f"- {d}" for d in design.architecture_decisions]
             + ["## API Changes"] + [f"- {c}" for c in design.api_changes]
             + ["## Components"] + [f"- {c}" for c in design.component_changes])
    return (
        f"You are an API Contract Generator.\n\n## Task\n\nProduce a **{fmt}** "
        f"contract for issue #{issue}.\n\n## Technical Design\n\n"
        + "\n".join(parts) + "\n\n## Rules\n\n"
        "1. Extract ALL endpoints/operations from the design.\n"
        "2. Include proper types and constraints for every field.\n"
        "3. Include ALL status codes (success AND error).\n"
        "4. Be precise — Test Writer generates assertions from this.\n"
        "5. Do NOT invent endpoints not in the design.\n"
        "6. Output ONLY the raw spec — no markdown fences, no preamble.\n")


def _process(raw: str, ct: ContractType, issue_number: int | str,
             state_dir: Path | None) -> ContractResult:
    content = strip_fences(raw)
    if not content.strip():
        return ContractResult(contract_type=ct, spec_content="",
                              validation_passed=False, raw_output=raw,
                              validation_messages=("Contract content is empty",))
    passed, msgs = validate_checks(content, _CHECKS.get(ct, []))
    ext = _META.get(ct, ("", ".yaml"))[1]
    out = save_artifact(content, f"api-contract{ext}", issue_number,
                        state_dir=state_dir)
    return ContractResult(contract_type=ct, spec_content=content,
                          validation_passed=passed,
                          validation_messages=tuple(msgs),
                          output_path=str(out), raw_output=raw)


def _err(ct: ContractType, raw: str = "", e: str = "") -> ContractResult:
    return ContractResult(contract_type=ct, spec_content="",
                          validation_passed=False, raw_output=raw,
                          errors=(e,) if e else ())


def generate_api_contract(  # noqa: PLR0913
    design: DesignResult, analysis: AnalysisResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None, issue_number: int | str = 0,
    workspace: str | Path | None = None,
) -> ContractResult:
    """Generate an API contract spec from *design* and *analysis*."""
    ws = Path(workspace) if workspace else Path(".")
    ct = _detect(ws)
    if ct == ContractType.NONE:
        ct = ContractType.OPENAPI if design.api_changes else ContractType.NONE
    if ct == ContractType.NONE:
        return ContractResult(contract_type=ct, spec_content="",
                              validation_passed=True,
                              validation_messages=("No API detected — skipped",))
    return run_generate(
        "Contract", _build_prompt(design, ct, issue_number),
        lambda raw: _process(raw, ct, issue_number, state_dir),
        lambda raw, e: _err(ct, raw, e),
        invoke_fn=invoke_fn,
    )
