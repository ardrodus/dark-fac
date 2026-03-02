"""API Explorer agent — discovers endpoints, routes, middleware, and auth mechanisms."""
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
_EXCLUDE = frozenset(
    "node_modules .git vendor dist __pycache__ .tox .venv .mypy_cache coverage build "
    "target .next .nuxt venv env .dark-factory".split())
_SRC_EXTS = frozenset((".py", ".js", ".ts", ".go", ".rs", ".java", ".cs"))
_AUTH_KW = ("jwt", "oauth", "bearer", "session", "passport", "api_key", "auth", "token")
_MW_KW = ("middleware", "app.use(", "cors", "rate.limit", "helmet", "compression")
_ROUTE_RE = re.compile(
    r"@(app|router|blueprint)\.(get|post|put|delete|patch|route)"
    r"|app\.(get|post|put|delete|patch|use)\s*\("
    r"|router\.(get|post|put|delete|patch|use)\s*\("
    r"|@(Get|Post|Put|Delete|Request)Mapping|\[Http(Get|Post|Put|Delete|Patch)\]"
    r"|http\.Handle|mux\.Handle|urlpatterns|path\(|re_path\("
    r"|web::(get|post|resource|route)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Single API endpoint or route."""
    method: str = ""
    path: str = ""
    handler: str = ""
    file: str = ""

@dataclass(frozen=True, slots=True)
class APIExplorerResult:
    """Structured output of the API Explorer agent."""
    endpoints: tuple[Endpoint, ...] = ()
    routes: tuple[str, ...] = ()
    middleware: tuple[str, ...] = ()
    auth_mechanisms: tuple[str, ...] = ()
    request_shapes: tuple[tuple[str, str], ...] = ()
    response_shapes: tuple[tuple[str, str], ...] = ()
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

def _collect_context(ws: Path, scout: ScoutResult) -> str:
    parts: list[str] = [f"## Scout Overview\n{scout.app_overview}\n"]
    if scout.entry_points:
        parts.append(f"Entry points: {', '.join(scout.entry_points)}\n")
    if scout.build_system:
        parts.append(f"Build system: {scout.build_system}\n")
    # Find route-containing files
    route_files: list[Path] = []
    rf_set: set[Path] = set()
    for f in ws.rglob("*"):
        if not (f.is_file() and f.suffix.lower() in _SRC_EXTS) or any(p in _EXCLUDE for p in f.parts):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")[:8000]
        except OSError:
            continue
        if _ROUTE_RE.search(content):
            route_files.append(f)
            rf_set.add(f)
    for rf in route_files[:15]:
        rel = str(rf.relative_to(ws))
        parts.append(f"## {rel}\n{_rd(rf)}\n")
    for f in ws.rglob("*"):
        if f in rf_set or not f.is_file() or f.suffix.lower() not in _SRC_EXTS:
            continue
        if any(p in _EXCLUDE for p in f.parts):
            continue
        if any(kw in f.stem.lower() for kw in (*_AUTH_KW, *_MW_KW)):
            parts.append(f"## {f.relative_to(ws)}\n{_rd(f)}\n")
    # OpenAPI / Swagger specs
    for name in ("openapi.yaml", "openapi.json", "swagger.json", "swagger.yaml"):
        content = _rd(ws / name)
        if content:
            parts.append(f"## {name}\n{content}\n")
    return "\n".join(parts)

def _build_prompt(context: str) -> str:
    return f"""\
You are the API Explorer Agent. Analyze the workspace data below and produce a JSON object \
documenting every public interface found.

{context}

## Output Format (strict JSON)
{{"endpoints": [{{"method":"GET","path":"/api/x","handler":"fn","file":"f.js"}},...],\
 "routes": ["GET /api/x",...], "middleware": ["cors",...],\
 "auth_mechanisms": ["JWT bearer",...],\
 "request_shapes": [["POST /api/x","{{name:str}}"],...],\
 "response_shapes": [["GET /api/x","{{items:[]}}"],...]}}
Be EXHAUSTIVE -- list ALL endpoints. Empty array if none. JSON only, no fences."""

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

def _parse_endpoints(raw_list: object) -> tuple[Endpoint, ...]:
    if not isinstance(raw_list, list):
        return ()
    eps: list[Endpoint] = []
    for e in raw_list:
        if isinstance(e, dict):
            eps.append(Endpoint(
                method=str(e.get("method", "")), path=str(e.get("path", "")),
                handler=str(e.get("handler", "")), file=str(e.get("file", "")),
            ))
    return tuple(eps)

def _pairs(data: dict[str, object], key: str) -> tuple[tuple[str, str], ...]:
    raw = data.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(
        (str(e[0]), str(e[1])) for e in raw
        if isinstance(e, (list, tuple)) and len(e) >= 2  # noqa: PLR2004
    )

def _parse_result(raw: str) -> APIExplorerResult:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\"endpoints\".*\}", text, re.DOTALL)
    if not match:
        match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return APIExplorerResult(raw_output=raw, errors=("no JSON found in output",))
    data: dict[str, object] = json.loads(match.group(0))
    return APIExplorerResult(
        endpoints=_parse_endpoints(data.get("endpoints")),
        routes=_strs(data, "routes"),
        middleware=_strs(data, "middleware"),
        auth_mechanisms=_strs(data, "auth_mechanisms"),
        request_shapes=_pairs(data, "request_shapes"),
        response_shapes=_pairs(data, "response_shapes"),
        raw_output=raw,
    )

def _save(result: APIExplorerResult, repo_name: str, *, state_dir: Path | None = None) -> Path:
    sd = (state_dir or _STATE_DIR) / "learning" / repo_name
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "api_explorer.json"
    payload = {k: v for k, v in asdict(result).items() if k != "raw_output"}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("API Explorer results saved to %s", out)
    return out

def run_api_explorer(
    workspace: Workspace, scout: ScoutResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> APIExplorerResult:
    """Discover endpoints, routes, middleware, auth mechanisms, request/response shapes."""
    ws_path = Path(workspace.path)
    repo_name = workspace.name or ws_path.name
    context = _collect_context(ws_path, scout)
    prompt = _build_prompt(context)
    try:
        raw = _invoke_agent(prompt, workspace.path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("API Explorer agent failed: %s", exc)
        return APIExplorerResult(raw_output="", errors=(str(exc),))
    try:
        result = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("API Explorer parse error: %s", exc)
        return APIExplorerResult(raw_output=raw, errors=(f"parse error: {exc}",))
    _save(result, repo_name, state_dir=state_dir)
    return result
