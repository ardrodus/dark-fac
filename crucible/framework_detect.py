"""Framework detection — analyze apps and determine test frameworks needed.

Examines a target application to determine which test frameworks are needed
for end-to-end Crucible validation, then ensures those frameworks exist in
the crucible test workspace.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum frameworks to recommend (complexity budget)
_MAX_FRAMEWORKS = 3


@dataclass(frozen=True, slots=True)
class FrameworkProfile:
    """Detected test framework configuration."""

    name: str  # e.g. "playwright", "cypress", "pytest", "jest"
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


# ── Framework Profiles ──────────────────────────────────────────

_PLAYWRIGHT = FrameworkProfile(
    name="playwright",
    language="TypeScript",
    install_cmd="npm install @playwright/test && npx playwright install",
    run_cmd="npx playwright test",
    config_file="playwright.config.ts",
    reporter_json="--reporter=json",
)

_CYPRESS = FrameworkProfile(
    name="cypress",
    language="TypeScript",
    install_cmd="npm install cypress",
    run_cmd="npx cypress run",
    config_file="cypress.config.ts",
    reporter_json="--reporter json",
)

_PYTEST = FrameworkProfile(
    name="pytest",
    language="Python",
    install_cmd="pip install pytest pytest-json-report",
    run_cmd="pytest",
    config_file="pytest.ini",
    reporter_json="--json-report --json-report-file=report.json",
)

_JEST = FrameworkProfile(
    name="jest",
    language="JavaScript",
    install_cmd="npm install jest",
    run_cmd="npx jest",
    config_file="jest.config.js",
    reporter_json="--json --outputFile=report.json",
)

_SUPERTEST = FrameworkProfile(
    name="supertest",
    language="TypeScript",
    install_cmd="npm install supertest @types/supertest",
    run_cmd="npx jest --testPathPattern=api",
    config_file="jest.config.js",
    reporter_json="--json",
)

_HTTPX = FrameworkProfile(
    name="httpx",
    language="Python",
    install_cmd="pip install httpx pytest",
    run_cmd="pytest tests/api/",
    config_file="pytest.ini",
    reporter_json="--json-report",
)

_PROFILES: dict[str, FrameworkProfile] = {
    "playwright": _PLAYWRIGHT,
    "cypress": _CYPRESS,
    "pytest": _PYTEST,
    "jest": _JEST,
    "supertest": _SUPERTEST,
    "httpx": _HTTPX,
}


# ── Detection Patterns ─────────────────────────────────────────

_WEB_UI_MARKERS = re.compile(
    r"react|vue|angular|svelte|next|nuxt|gatsby|remix|astro"
    r"|\.html|\.ejs|\.hbs|\.pug|\.jinja|templates/",
    re.I,
)

_API_MARKERS = re.compile(
    r"express|fastapi|flask|django|gin|actix|spring.*boot"
    r"|routes?/|controllers?/|api/|endpoints?/|openapi|swagger",
    re.I,
)

_CLI_MARKERS = re.compile(
    r"argparse|click|typer|clap|commander|yargs|cobra|cli/|__main__\.py",
    re.I,
)

# ── Framework selection based on app language ───────────────────

_LANG_WEB_FW: dict[str, str] = {
    "TypeScript": "playwright",
    "JavaScript": "playwright",
    "Python": "playwright",
    "Go": "playwright",
    "Rust": "playwright",
    "Java": "playwright",
    "C#": "playwright",
}

_LANG_API_FW: dict[str, str] = {
    "TypeScript": "supertest",
    "JavaScript": "supertest",
    "Python": "httpx",
    "Go": "httpx",
    "Rust": "httpx",
    "Java": "httpx",
    "C#": "httpx",
}


def _rd(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _detect_web_ui(root: Path) -> bool:
    """Check for HTML templates, React/Vue/Angular, static assets."""
    for marker in ("public/index.html", "src/App.tsx", "src/App.jsx",
                    "src/App.vue", "src/App.svelte", "templates/",
                    "src/pages/", "app/page.tsx", "app/page.jsx"):
        if (root / marker).exists():
            return True
    pkg = _rd(root / "package.json").lower()
    if _WEB_UI_MARKERS.search(pkg):
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
        if _API_MARKERS.search(_rd(root / cfg)):
            return True
    return False


def _detect_cli(root: Path) -> bool:
    """Check for argparse, clap, commander patterns."""
    for cfg in ("package.json", "pyproject.toml", "Cargo.toml"):
        if _CLI_MARKERS.search(_rd(root / cfg)):
            return True
    if (root / "__main__.py").is_file() or (root / "src" / "__main__.py").is_file():
        return True
    return False


def _scan_existing(crucible_path: Path) -> list[str]:
    """Check what frameworks already exist in the crucible repo."""
    found: list[str] = []
    pkg = _rd(crucible_path / "package.json")
    if "@playwright/test" in pkg:
        found.append("playwright")
    if "cypress" in pkg:
        found.append("cypress")
    if "jest" in pkg or "@jest" in pkg:
        found.append("jest")
    if "supertest" in pkg:
        found.append("supertest")
    # Python
    reqs = _rd(crucible_path / "requirements.txt")
    pyproj = _rd(crucible_path / "pyproject.toml")
    if "pytest" in reqs or "pytest" in pyproj:
        found.append("pytest")
    if "httpx" in reqs or "httpx" in pyproj:
        found.append("httpx")
    return found


def _app_own_framework(root: Path) -> str | None:
    """Prefer the test framework the app team already uses."""
    pkg = _rd(root / "package.json")
    if "@playwright/test" in pkg:
        return "playwright"
    if "cypress" in pkg:
        return "cypress"
    if "jest" in pkg:
        return "jest"
    reqs = _rd(root / "requirements.txt") + _rd(root / "pyproject.toml")
    if "pytest" in reqs:
        return "pytest"
    return None


# ── Public API ──────────────────────────────────────────────────


def detect_frameworks(
    workspace_path: str | Path,
    crucible_path: str | Path,
    *,
    language: str = "",
    framework: str = "",
    has_web_server: bool | None = None,
) -> DetectionResult:
    """Analyze app in workspace, compare against crucible repo frameworks.

    Args:
        workspace_path: Path to the application workspace.
        crucible_path: Path to the crucible test repository.
        language: Override language detection (from project_analyzer).
        framework: Override framework detection (from project_analyzer).
        has_web_server: Override web server detection.
    """
    root = Path(workspace_path)
    cruc = Path(crucible_path)

    # Use project_analyzer if language not provided
    app_lang = language
    app_fw = framework
    if not app_lang:
        try:
            from dark_factory.setup.project_analyzer import analyze_project  # noqa: PLC0415
            analysis = analyze_project(str(root))
            app_lang = analysis.language
            app_fw = app_fw or analysis.framework
            if has_web_server is None:
                has_web_server = analysis.has_web_server
        except Exception:  # noqa: BLE001
            app_lang = ""

    has_web = has_web_server if has_web_server is not None else _detect_web_ui(root)
    has_api = _detect_api(root)
    has_cli = _detect_cli(root)

    # Build recommended frameworks list
    recommended: list[FrameworkProfile] = []

    # Prefer app's own test framework
    own_fw = _app_own_framework(root)
    if own_fw and own_fw in _PROFILES:
        recommended.append(_PROFILES[own_fw])

    # Web UI -> Playwright (unless app uses Cypress)
    if has_web and not any(f.name in ("playwright", "cypress") for f in recommended):
        fw_name = _LANG_WEB_FW.get(app_lang, "playwright")
        recommended.append(_PROFILES[fw_name])

    # API -> supertest/httpx
    if has_api and not any(f.name in ("supertest", "httpx") for f in recommended):
        fw_name = _LANG_API_FW.get(app_lang, "httpx")
        recommended.append(_PROFILES[fw_name])

    # If nothing detected, default to language's standard runner
    if not recommended:
        if app_lang in ("TypeScript", "JavaScript"):
            recommended.append(_PLAYWRIGHT)
        elif app_lang == "Python":
            recommended.append(_PYTEST)
        else:
            recommended.append(_PLAYWRIGHT)  # universal fallback

    # Cap at MAX_FRAMEWORKS
    recommended = recommended[:_MAX_FRAMEWORKS]

    # Compare against existing crucible frameworks
    existing = _scan_existing(cruc)
    rec_names = [f.name for f in recommended]
    missing = [n for n in rec_names if n not in existing]
    install = [_PROFILES[n].install_cmd for n in missing if n in _PROFILES]

    logger.info(
        "Framework detection: lang=%s fw=%s web=%s api=%s cli=%s "
        "recommended=%s existing=%s missing=%s",
        app_lang, app_fw, has_web, has_api, has_cli,
        rec_names, existing, missing,
    )

    return DetectionResult(
        app_language=app_lang,
        app_framework=app_fw,
        has_web_ui=has_web,
        has_api=has_api,
        has_cli=has_cli,
        recommended_frameworks=tuple(recommended),
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
