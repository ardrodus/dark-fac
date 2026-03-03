"""Framework detection utilities for Crucible."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum frameworks to recommend (complexity budget)
_MAX_FRAMEWORKS = 3


@dataclass(frozen=True, slots=True)
class FrameworkProfile:
    """Detected test framework configuration."""

    name: str  # e.g. "playwright", "cypress", "supertest", "httpx"
    language: str  # e.g. "TypeScript", "Python", "JavaScript"
    install_cmd: str  # e.g. "npm install @playwright/test"
    run_cmd: str  # e.g. "npx playwright test"
    config_file: str  # e.g. "playwright.config.ts"
    reporter_json: str  # flag/option to produce JSON output


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Full framework detection result."""

    app_language: str
    app_framework: str
    has_web_ui: bool
    has_api: bool
    has_cli: bool
    recommended_frameworks: tuple[FrameworkProfile, ...]
    existing_frameworks: tuple[str, ...] = ()
    missing_frameworks: tuple[str, ...] = ()
    install_actions: tuple[str, ...] = ()


# ── Known Framework Profiles (E2E/integration only) ──────────

_PROFILES: dict[str, FrameworkProfile] = {
    "playwright": FrameworkProfile(
        name="playwright", language="TypeScript",
        install_cmd="npm install @playwright/test && npx playwright install",
        run_cmd="npx playwright test",
        config_file="playwright.config.ts",
        reporter_json="--reporter=json",
    ),
    "cypress": FrameworkProfile(
        name="cypress", language="TypeScript",
        install_cmd="npm install cypress",
        run_cmd="npx cypress run",
        config_file="cypress.config.ts",
        reporter_json="--reporter json",
    ),
    "supertest": FrameworkProfile(
        name="supertest", language="TypeScript",
        install_cmd="npm install supertest @types/supertest jest",
        run_cmd="npx jest --testPathPattern=api",
        config_file="jest.config.js",
        reporter_json="--json",
    ),
    "httpx": FrameworkProfile(
        name="httpx", language="Python",
        install_cmd="pip install httpx pytest pytest-json-report",
        run_cmd="pytest tests/api/",
        config_file="pytest.ini",
        reporter_json="--json-report",
    ),
    "selenium": FrameworkProfile(
        name="selenium", language="Python",
        install_cmd="pip install selenium pytest",
        run_cmd="pytest tests/e2e/",
        config_file="pytest.ini",
        reporter_json="--json-report",
    ),
}

# Fallback profile when agent recommends something we don't have a profile for
_DEFAULT_PROFILE = _PROFILES["playwright"]

# ── Detection Patterns ─────────────────────────────────────────

_API_MARKERS = re.compile(
    r"express|fastapi|flask|django|gin|actix|spring.*boot"
    r"|routes?/|controllers?/|api/|endpoints?/|openapi|swagger",
    re.I,
)

_CLI_MARKERS = re.compile(
    r"argparse|click|typer|clap|commander|yargs|cobra|cli/|__main__\.py",
    re.I,
)


# ── Response Parsing (deterministic) ─────────────────────────

_DETECTION_PATTERN = re.compile(
    r"<<<FRAMEWORK_DETECTION>>>\s*\n?(.*?)\n?<<<END_FRAMEWORK_DETECTION>>>",
    re.S,
)


def _parse_agent_response(response: str) -> dict[str, Any]:
    """Extract the structured JSON from the agent's response."""
    m = _DETECTION_PATTERN.search(response)
    if not m:
        # Try to find raw JSON as fallback
        try:
            start = response.index("{")
            return json.loads(response[start:])
        except (ValueError, json.JSONDecodeError):
            return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _resolve_profile(name: str, language: str = "") -> FrameworkProfile:
    """Look up a known framework profile by name."""
    if name in _PROFILES:
        return _PROFILES[name]
    # Agent recommended something we don't have a profile for — use default
    logger.warning("Unknown framework '%s', using default profile", name)
    return _DEFAULT_PROFILE


def _read_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:5000]
    except OSError:
        return ""


def _detect_web_ui(root: Path) -> bool:
    """Check for HTML templates, React/Vue/Angular, static assets."""
    for marker in ("public/index.html", "src/App.tsx", "src/App.jsx",
                    "src/App.vue", "src/App.svelte", "templates/",
                    "src/pages/", "app/page.tsx", "app/page.jsx"):
        if (root / marker).exists():
            return True
    pkg = _read_safe(root / "package.json").lower()
    if pkg and re.search(r"next|nuxt|gatsby|remix|astro|react|vue|angular|svelte", pkg):
        return True
    return False


def _detect_api(root: Path) -> bool:
    """Check for route definitions, OpenAPI specs, controller patterns."""
    for marker in ("openapi.yaml", "openapi.yml", "swagger.json",
                    "swagger.yaml", "routes/", "api/", "controllers/"):
        if (root / marker).exists():
            return True
    for cfg in ("package.json", "pyproject.toml", "requirements.txt",
                "Cargo.toml", "go.mod"):
        if _API_MARKERS.search(_read_safe(root / cfg)):
            return True
    return False


def _detect_cli(root: Path) -> bool:
    """Check for argparse, click, typer, clap, commander patterns."""
    for cfg in ("package.json", "pyproject.toml", "Cargo.toml"):
        if _CLI_MARKERS.search(_read_safe(root / cfg)):
            return True
    if (root / "__main__.py").is_file() or (root / "src" / "__main__.py").is_file():
        return True
    return False


def _scan_existing(cruc: Path) -> list[str]:
    """Quick check of what frameworks exist in the crucible repo."""
    found: list[str] = []
    pkg = _read_safe(cruc / "package.json")
    if "@playwright/test" in pkg:
        found.append("playwright")
    if "cypress" in pkg:
        found.append("cypress")
    if "supertest" in pkg:
        found.append("supertest")
    reqs = _read_safe(cruc / "requirements.txt")
    pyproj = _read_safe(cruc / "pyproject.toml")
    combined = reqs + pyproj
    if "httpx" in combined:
        found.append("httpx")
    if "pytest" in reqs or "pytest" in pyproj:
        found.append("pytest")
    return found


# Keep backward-compatible alias
_scan_existing_simple = _scan_existing


# ── Build DetectionResult from agent output ──────────────────


def build_detection_result(
    response: str,
    crucible_path: str | Path,
    *,
    language: str = "",
    framework: str = "",
) -> DetectionResult:
    """Parse an agent's framework detection response into a DetectionResult.

    Called after the pipeline engine runs the detect_frameworks node and
    captures the agent output.  This function handles only deterministic
    post-processing: JSON parsing, profile lookup, install resolution.
    """
    cruc = Path(crucible_path)
    data = _parse_agent_response(response)
    if not data:
        logger.warning("Could not parse agent response, using defaults")
        return _detect_fallback_simple(cruc, language, framework)

    app_lang = str(data.get("app_language", language or "Unknown"))
    app_fw = str(data.get("app_framework", framework or "Unknown"))
    has_web = bool(data.get("has_web_ui", False))
    has_api = bool(data.get("has_api", False))
    has_cli = bool(data.get("has_cli", False))

    # Resolve recommended frameworks to profiles
    recommended: list[FrameworkProfile] = []
    for rec in data.get("recommended", []):
        if isinstance(rec, dict):
            name = str(rec.get("name", ""))
            lang = str(rec.get("language", ""))
            if name:
                recommended.append(_resolve_profile(name, lang))
    recommended = recommended[:_MAX_FRAMEWORKS]

    if not recommended:
        recommended.append(_DEFAULT_PROFILE)

    # Cross-check with actual crucible repo
    existing = _scan_existing_simple(cruc)
    rec_names = [f.name for f in recommended]
    missing = [n for n in rec_names if n not in existing]
    install = [_PROFILES[n].install_cmd for n in missing if n in _PROFILES]

    return DetectionResult(
        app_language=app_lang, app_framework=app_fw,
        has_web_ui=has_web, has_api=has_api, has_cli=has_cli,
        recommended_frameworks=tuple(recommended),
        existing_frameworks=tuple(existing),
        missing_frameworks=tuple(missing),
        install_actions=tuple(install),
    )


# ── Fallback (when no agent available) ───────────────────────


def _detect_fallback_simple(
    cruc: Path, language: str, framework: str,
) -> DetectionResult:
    """Minimal defaults when parsing fails."""
    lang = language or "Unknown"
    rec: list[FrameworkProfile] = []
    if lang in ("TypeScript", "JavaScript"):
        rec.append(_PROFILES["playwright"])
    elif lang == "Python":
        rec.append(_PROFILES["httpx"])
    else:
        rec.append(_PROFILES["playwright"])

    existing = _scan_existing_simple(cruc)
    rec_names = [f.name for f in rec]
    missing = [n for n in rec_names if n not in existing]
    install = [_PROFILES[n].install_cmd for n in missing if n in _PROFILES]

    return DetectionResult(
        app_language=lang, app_framework=framework,
        has_web_ui=lang != "Python", has_api=lang == "Python", has_cli=False,
        recommended_frameworks=tuple(rec),
        existing_frameworks=tuple(existing),
        missing_frameworks=tuple(missing),
        install_actions=tuple(install),
    )


def detect_frameworks(
    workspace_path: str | Path,
    crucible_path: str | Path,
    *,
    language: str = "",
    framework: str = "",
    has_web_server: bool | None = None,
    agent_fn: None = None,
) -> DetectionResult:
    """Deterministic framework detection fallback.

    When called via the pipeline engine, the agent runs in crucible.dot
    and the response is parsed by ``build_detection_result()``.
    This function provides the deterministic fallback for the coordinator
    path (no agent available).
    """
    root = Path(workspace_path)
    cruc = Path(crucible_path)
    lang = language or "Unknown"

    has_web = has_web_server if has_web_server is not None else _detect_web_ui(root)
    has_api = _detect_api(root)
    has_cli = _detect_cli(root)

    rec: list[FrameworkProfile] = []

    # Web UI -> Playwright
    if has_web:
        if lang in ("TypeScript", "JavaScript"):
            rec.append(_PROFILES["playwright"])
        else:
            rec.append(_PROFILES["playwright"])

    # API -> supertest (TS/JS) or httpx (Python)
    if has_api and not any(f.name in ("supertest", "httpx") for f in rec):
        if lang in ("TypeScript", "JavaScript"):
            rec.append(_PROFILES["supertest"])
        else:
            rec.append(_PROFILES["httpx"])

    # If nothing detected, default to language's standard runner
    if not rec:
        if lang in ("TypeScript", "JavaScript"):
            rec.append(_PROFILES["playwright"])
        elif lang == "Python":
            rec.append(_PROFILES["httpx"])
        else:
            rec.append(_PROFILES["playwright"])

    rec = rec[:_MAX_FRAMEWORKS]
    existing = _scan_existing(cruc)
    rec_names = [f.name for f in rec]
    missing = [n for n in rec_names if n not in existing]
    install = [_PROFILES[n].install_cmd for n in missing if n in _PROFILES]

    return DetectionResult(
        app_language=lang, app_framework=framework,
        has_web_ui=has_web, has_api=has_api, has_cli=has_cli,
        recommended_frameworks=tuple(rec),
        existing_frameworks=tuple(existing),
        missing_frameworks=tuple(missing),
        install_actions=tuple(install),
    )


def ensure_frameworks(
    crucible_path: str | Path,
    result: DetectionResult,
    *,
    shell_fn: Any = None,
) -> bool:
    """Install missing frameworks into the crucible workspace.

    Returns ``True`` if all installations succeed (or nothing needed).
    """
    if not result.missing_frameworks:
        logger.info("No missing frameworks to install")
        return True

    cruc = Path(crucible_path)
    if not cruc.is_dir():
        logger.error("Crucible path does not exist: %s", cruc)
        return False

    all_ok = True
    for action in result.install_actions:
        logger.info("Installing: %s (in %s)", action, cruc)
        try:
            if shell_fn:
                r = shell_fn(action.split(), cwd=str(cruc))
                if hasattr(r, "returncode") and r.returncode != 0:
                    logger.error("Install failed: %s", action)
                    all_ok = False
            else:
                from dark_factory.integrations.shell import run  # noqa: PLC0415
                r = run(action.split(), cwd=str(cruc))
                if r.returncode != 0:
                    logger.error("Install failed: %s -> %s", action, r.stderr.strip())
                    all_ok = False
        except Exception as exc:  # noqa: BLE001
            logger.error("Install error: %s -> %s", action, exc)
            all_ok = False

    return all_ok
