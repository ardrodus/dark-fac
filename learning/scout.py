"""Scout agent — discovers project structure, entry points, and key abstractions."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT, _STATE_DIR = 300, Path(".dark-factory")
_CODE_EXTS = frozenset(
    ".py .js .jsx .ts .tsx .go .rs .java .cs .cpp .c .h .rb .php .swift .kt .scala".split())
_EXCLUDE = frozenset(
    "node_modules .git vendor dist __pycache__ .tox .venv .mypy_cache coverage build "
    "target .next .nuxt venv env .dark-factory".split())
_MANIFESTS = (
    "package.json", "Cargo.toml", "go.mod", "pyproject.toml", "pom.xml",
    "build.gradle", "Gemfile", "composer.json", "requirements.txt", "setup.py")
_BUILD_CONFIGS = (
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "Taskfile.yml", "justfile", "Rakefile", "Jenkinsfile", ".gitlab-ci.yml")
_ENTRY_PATS = ("main.*", "index.*", "app.*", "server.*", "__main__.py", "cli.*")

@dataclass(frozen=True, slots=True)
class ScoutResult:
    """Structured output of the Scout agent."""
    directory_layout: tuple[tuple[str, str], ...] = ()
    entry_points: tuple[str, ...] = ()
    config_files: tuple[str, ...] = ()
    build_system: str = ""
    key_abstractions: tuple[str, ...] = ()
    agents_needed: tuple[str, ...] = ()
    app_overview: str = ""
    file_counts: tuple[tuple[str, int], ...] = ()
    raw_output: str = ""
    errors: tuple[str, ...] = ()


def _rd(p: Path, limit: int = 4000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""

def _strs(data: dict[str, object], key: str) -> tuple[str, ...]:
    raw = data.get(key)
    return tuple(str(e) for e in raw) if isinstance(raw, list) else ()

def _collect_context(ws: Path) -> str:
    parts: list[str] = []
    try:
        entries = sorted(ws.iterdir())
    except OSError:
        entries = []
    dirs = [e.name + "/" for e in entries if e.is_dir() and e.name not in _EXCLUDE]
    files = [e.name for e in entries if e.is_file()]
    parts.append(f"## Top-level\nDirs: {', '.join(dirs)}\nFiles: {', '.join(files)}\n")
    for pat in ("README*", "CONTRIBUTING*", "LICENSE*", "CHANGELOG*"):
        for hit in ws.glob(pat):
            parts.append(f"## {hit.name}\n{_rd(hit)}\n")
    for names in (_MANIFESTS, _BUILD_CONFIGS, ("CLAUDE.md", ".cursorrules")):
        for name in names:
            content = _rd(ws / name)
            if content:
                parts.append(f"## {name}\n{content}\n")
    wf = ws / ".github" / "workflows"
    if wf.is_dir():
        for f in sorted(wf.iterdir())[:5]:
            parts.append(f"## .github/workflows/{f.name}\n{_rd(f, 2000)}\n")
    counts: Counter[str] = Counter()
    for f in ws.rglob("*"):
        if f.is_file() and not any(p in _EXCLUDE for p in f.parts):
            if f.suffix.lower() in _CODE_EXTS:
                counts[f.suffix.lower()] += 1
    if counts:
        parts.append("## File counts\n" + ", ".join(
            f"{ext}: {n}" for ext, n in counts.most_common(15)) + "\n")
    found: list[str] = []
    for pat in _ENTRY_PATS:
        for hit in ws.glob(pat):
            if hit.is_file() and hit.suffix.lower() in _CODE_EXTS:
                found.append(str(hit.relative_to(ws)))
    for sub in ("src", "lib", "app", "cmd"):
        sd = ws / sub
        if sd.is_dir():
            for pat in _ENTRY_PATS:
                found.extend(str(h.relative_to(ws)) for h in sd.glob(pat) if h.is_file())
    if found:
        parts.append(f"## Entry point candidates\n{', '.join(found[:20])}\n")
    return "\n".join(parts)

def _build_prompt(context: str) -> str:
    return f"""\
You are the Scout Agent. Analyze the workspace data below and produce a JSON object.

{context}

## Output Format (strict JSON)
{{
  "app_overview": "one-paragraph project summary",
  "directory_layout": [["dir_name/", "purpose"], ...],
  "entry_points": ["path/to/main.py", ...],
  "config_files": ["pyproject.toml", ...],
  "build_system": "description of build tool(s) and key commands",
  "key_abstractions": ["pattern or abstraction description", ...],
  "agents_needed": ["architect", ...]
}}

Rules for agents_needed (choose from: architect, api-explorer, domain-expert, \
data-mapper, integration-analyst, test-archaeologist):
- architect: ALWAYS include
- api-explorer: if HTTP routes, REST/GraphQL API, or CLI commands exist
- domain-expert: if business logic or domain models exist
- data-mapper: if database, ORM, or migrations exist
- integration-analyst: if external services or cloud SDKs exist
- test-archaeologist: if test files exist

Output ONLY the JSON object, no markdown fences."""

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

def _parse_result(raw: str) -> ScoutResult:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\"app_overview\".*\}", text, re.DOTALL)
    if not match:
        match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return ScoutResult(raw_output=raw, errors=("no JSON found in output",))
    data: dict[str, object] = json.loads(match.group(0))
    raw_layout = data.get("directory_layout")
    layout = tuple(
        (str(e[0]), str(e[1])) for e in raw_layout  # type: ignore[union-attr]
        if isinstance(e, (list, tuple)) and len(e) >= 2  # noqa: PLR2004
    ) if isinstance(raw_layout, list) else ()
    return ScoutResult(
        app_overview=str(data.get("app_overview", "")),
        directory_layout=layout,
        entry_points=_strs(data, "entry_points"),
        config_files=_strs(data, "config_files"),
        build_system=str(data.get("build_system", "")),
        key_abstractions=_strs(data, "key_abstractions"),
        agents_needed=_strs(data, "agents_needed"),
        raw_output=raw,
    )

def _save(result: ScoutResult, repo_name: str, *, state_dir: Path | None = None) -> Path:
    sd = (state_dir or _STATE_DIR) / "learning" / repo_name
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "scout.json"
    payload = {k: v for k, v in asdict(result).items() if k != "raw_output"}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Scout results saved to %s", out)
    return out

def run_scout(
    workspace: Workspace, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> ScoutResult:
    """Discover project structure, entry points, config files, build system, key abstractions."""
    ws_path = Path(workspace.path)
    repo_name = workspace.name or ws_path.name
    context = _collect_context(ws_path)
    prompt = _build_prompt(context)
    try:
        raw = _invoke_agent(prompt, workspace.path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Scout agent failed: %s", exc)
        return ScoutResult(raw_output="", errors=(str(exc),))
    try:
        result = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Scout parse error: %s", exc)
        return ScoutResult(raw_output=raw, errors=(f"parse error: {exc}",))
    _save(result, repo_name, state_dir=state_dir)
    return result
