"""Tests for crucible/framework_detect.py — deterministic utilities."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dark_factory.crucible.framework_detect import (
    DetectionResult,
    FrameworkProfile,
    _parse_agent_response,
    _resolve_profile,
    _scan_existing_simple,
    build_detection_result,
    detect_frameworks,
    ensure_frameworks,
)


# ── Response Parsing ─────────────────────────────────────────


class TestParseAgentResponse:
    def test_parses_valid_response(self) -> None:
        response = '''Here's my analysis...

<<<FRAMEWORK_DETECTION>>>
{
  "app_language": "TypeScript",
  "app_framework": "Next.js",
  "has_web_ui": true,
  "has_api": true,
  "has_cli": false,
  "recommended": [
    {"name": "playwright", "language": "TypeScript", "reason": "Web UI"},
    {"name": "supertest", "language": "TypeScript", "reason": "API"}
  ],
  "already_installed": ["playwright"],
  "to_install": ["supertest"]
}
<<<END_FRAMEWORK_DETECTION>>>'''
        data = _parse_agent_response(response)
        assert data["app_language"] == "TypeScript"
        assert len(data["recommended"]) == 2
        assert data["recommended"][0]["name"] == "playwright"

    def test_fallback_to_raw_json(self) -> None:
        response = '{"app_language": "Python", "recommended": []}'
        data = _parse_agent_response(response)
        assert data["app_language"] == "Python"

    def test_unparseable_returns_empty(self) -> None:
        data = _parse_agent_response("I don't know what frameworks to use.")
        assert data == {}


class TestResolveProfile:
    def test_known_framework(self) -> None:
        profile = _resolve_profile("playwright")
        assert profile.name == "playwright"
        assert "npx playwright" in profile.run_cmd

    def test_unknown_framework_uses_default(self) -> None:
        profile = _resolve_profile("some-unknown-fw")
        assert profile.name == "playwright"  # default fallback


class TestScanExistingSimple:
    def test_detects_playwright(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"@playwright/test": "^1"}}),
            encoding="utf-8")
        assert "playwright" in _scan_existing_simple(tmp_path)

    def test_detects_httpx(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("httpx\npytest\n", encoding="utf-8")
        assert "httpx" in _scan_existing_simple(tmp_path)

    def test_empty_crucible(self, tmp_path: Path) -> None:
        assert _scan_existing_simple(tmp_path) == []


# ── Build DetectionResult from agent output ──────────────────


class TestBuildDetectionResult:
    def test_parses_agent_output(self, tmp_path: Path) -> None:
        response = '''<<<FRAMEWORK_DETECTION>>>
{
  "app_language": "TypeScript",
  "app_framework": "Next.js",
  "has_web_ui": true,
  "has_api": true,
  "has_cli": false,
  "recommended": [
    {"name": "playwright", "language": "TypeScript", "reason": "Web UI E2E"},
    {"name": "supertest", "language": "TypeScript", "reason": "API testing"}
  ],
  "already_installed": [],
  "to_install": ["playwright", "supertest"]
}
<<<END_FRAMEWORK_DETECTION>>>'''
        result = build_detection_result(response, tmp_path)
        assert result.app_language == "TypeScript"
        assert result.has_web_ui is True
        assert result.has_api is True
        assert any(f.name == "playwright" for f in result.recommended_frameworks)
        assert any(f.name == "supertest" for f in result.recommended_frameworks)

    def test_unparseable_uses_defaults(self, tmp_path: Path) -> None:
        result = build_detection_result("garbage", tmp_path, language="Python")
        assert len(result.recommended_frameworks) >= 1

    def test_max_three_frameworks(self, tmp_path: Path) -> None:
        response = '''<<<FRAMEWORK_DETECTION>>>
{
  "app_language": "TypeScript", "app_framework": "Next.js",
  "has_web_ui": true, "has_api": true, "has_cli": false,
  "recommended": [
    {"name": "playwright", "language": "TypeScript", "reason": "UI"},
    {"name": "cypress", "language": "TypeScript", "reason": "UI alt"},
    {"name": "supertest", "language": "TypeScript", "reason": "API"},
    {"name": "httpx", "language": "Python", "reason": "backup"}
  ],
  "already_installed": [], "to_install": []
}
<<<END_FRAMEWORK_DETECTION>>>'''
        result = build_detection_result(response, tmp_path)
        assert len(result.recommended_frameworks) <= 3

    def test_existing_not_in_missing(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"@playwright/test": "^1"}}),
            encoding="utf-8")
        response = '''<<<FRAMEWORK_DETECTION>>>
{
  "app_language": "TypeScript", "app_framework": "Next.js",
  "has_web_ui": true, "has_api": false, "has_cli": false,
  "recommended": [{"name": "playwright", "language": "TypeScript", "reason": "UI"}],
  "already_installed": ["playwright"], "to_install": []
}
<<<END_FRAMEWORK_DETECTION>>>'''
        result = build_detection_result(response, tmp_path)
        assert "playwright" not in result.missing_frameworks


# ── Deterministic Fallback ───────────────────────────────────


class TestDetectFrameworks:
    def test_fallback_typescript(self, tmp_path: Path) -> None:
        app = tmp_path / "app"
        app.mkdir()
        (app / "package.json").write_text("{}", encoding="utf-8")
        cruc = tmp_path / "cruc"
        cruc.mkdir()
        result = detect_frameworks(app, cruc, language="TypeScript")
        assert len(result.recommended_frameworks) >= 1
        assert result.recommended_frameworks[0].name == "playwright"

    def test_fallback_python(self, tmp_path: Path) -> None:
        app = tmp_path / "app"
        app.mkdir()
        cruc = tmp_path / "cruc"
        cruc.mkdir()
        result = detect_frameworks(app, cruc, language="Python")
        assert len(result.recommended_frameworks) >= 1
        assert result.recommended_frameworks[0].name == "httpx"

    def test_default_when_nothing_detected(self, tmp_path: Path) -> None:
        app = tmp_path / "app"
        app.mkdir()
        cruc = tmp_path / "cruc"
        cruc.mkdir()
        result = detect_frameworks(app, cruc, language="")
        assert len(result.recommended_frameworks) >= 1


# ── Install ──────────────────────────────────────────────────


class TestEnsureFrameworks:
    def test_nothing_to_install(self, tmp_path: Path) -> None:
        result = DetectionResult(
            app_language="TypeScript", app_framework="Next.js",
            has_web_ui=True, has_api=False, has_cli=False,
            recommended_frameworks=(), missing_frameworks=(),
            install_actions=(),
        )
        assert ensure_frameworks(tmp_path, result) is True

    def test_install_called(self, tmp_path: Path) -> None:
        calls: list[tuple] = []

        class FakeResult:
            returncode = 0

        def mock_shell(args, cwd=""):
            calls.append((args, cwd))
            return FakeResult()

        result = DetectionResult(
            app_language="TypeScript", app_framework="Next.js",
            has_web_ui=True, has_api=False, has_cli=False,
            recommended_frameworks=(),
            missing_frameworks=("playwright",),
            install_actions=("npm install @playwright/test",),
        )
        ok = ensure_frameworks(tmp_path, result, shell_fn=mock_shell)
        assert ok is True
        assert len(calls) == 1
