"""Tests for crucible/framework_detect.py — framework detection and installation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dark_factory.crucible.framework_detect import (
    DetectionResult,
    FrameworkProfile,
    _detect_api,
    _detect_cli,
    _detect_web_ui,
    _scan_existing,
    detect_frameworks,
)


@pytest.fixture()
def app_dir(tmp_path: Path) -> Path:
    d = tmp_path / "app"
    d.mkdir()
    return d


@pytest.fixture()
def crucible_dir(tmp_path: Path) -> Path:
    d = tmp_path / "crucible"
    d.mkdir()
    return d


class TestDetectWebUI:
    def test_react_app(self, app_dir: Path) -> None:
        (app_dir / "src").mkdir()
        (app_dir / "src" / "App.tsx").touch()
        assert _detect_web_ui(app_dir) is True

    def test_vue_app(self, app_dir: Path) -> None:
        (app_dir / "src").mkdir()
        (app_dir / "src" / "App.vue").touch()
        assert _detect_web_ui(app_dir) is True

    def test_next_in_package_json(self, app_dir: Path) -> None:
        (app_dir / "package.json").write_text(
            json.dumps({"dependencies": {"next": "^14.0.0"}}), encoding="utf-8")
        assert _detect_web_ui(app_dir) is True

    def test_no_web_ui(self, app_dir: Path) -> None:
        (app_dir / "main.py").write_text("print('hello')", encoding="utf-8")
        assert _detect_web_ui(app_dir) is False


class TestDetectAPI:
    def test_express_in_package(self, app_dir: Path) -> None:
        (app_dir / "package.json").write_text(
            json.dumps({"dependencies": {"express": "^4.0.0"}}), encoding="utf-8")
        assert _detect_api(app_dir) is True

    def test_fastapi_in_requirements(self, app_dir: Path) -> None:
        (app_dir / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
        assert _detect_api(app_dir) is True

    def test_routes_directory(self, app_dir: Path) -> None:
        (app_dir / "routes").mkdir()
        assert _detect_api(app_dir) is True

    def test_openapi_spec(self, app_dir: Path) -> None:
        (app_dir / "openapi.yaml").touch()
        assert _detect_api(app_dir) is True

    def test_no_api(self, app_dir: Path) -> None:
        assert _detect_api(app_dir) is False


class TestDetectCLI:
    def test_argparse_in_pyproject(self, app_dir: Path) -> None:
        (app_dir / "pyproject.toml").write_text(
            '[project]\ndependencies = ["click"]\n', encoding="utf-8")
        assert _detect_cli(app_dir) is True

    def test_main_py(self, app_dir: Path) -> None:
        (app_dir / "__main__.py").write_text("", encoding="utf-8")
        assert _detect_cli(app_dir) is True

    def test_no_cli(self, app_dir: Path) -> None:
        assert _detect_cli(app_dir) is False


class TestScanExisting:
    def test_playwright_detected(self, crucible_dir: Path) -> None:
        (crucible_dir / "package.json").write_text(
            json.dumps({"devDependencies": {"@playwright/test": "^1.40.0"}}),
            encoding="utf-8")
        assert "playwright" in _scan_existing(crucible_dir)

    def test_pytest_detected(self, crucible_dir: Path) -> None:
        (crucible_dir / "requirements.txt").write_text("pytest\n", encoding="utf-8")
        assert "pytest" in _scan_existing(crucible_dir)

    def test_empty_crucible(self, crucible_dir: Path) -> None:
        assert _scan_existing(crucible_dir) == []


class TestDetectFrameworks:
    def test_node_web_app(self, app_dir: Path, crucible_dir: Path) -> None:
        (app_dir / "package.json").write_text(
            json.dumps({"dependencies": {"next": "^14", "react": "^18"}}),
            encoding="utf-8")
        (app_dir / "src").mkdir()
        (app_dir / "src" / "App.tsx").touch()
        result = detect_frameworks(
            app_dir, crucible_dir, language="TypeScript", framework="Next.js",
        )
        assert result.has_web_ui is True
        assert any(f.name == "playwright" for f in result.recommended_frameworks)

    def test_python_api(self, app_dir: Path, crucible_dir: Path) -> None:
        (app_dir / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
        result = detect_frameworks(
            app_dir, crucible_dir, language="Python", framework="FastAPI",
        )
        assert result.has_api is True
        assert any(f.name == "httpx" for f in result.recommended_frameworks)

    def test_missing_frameworks_identified(self, app_dir: Path, crucible_dir: Path) -> None:
        (app_dir / "package.json").write_text(
            json.dumps({"dependencies": {"express": "^4"}}), encoding="utf-8")
        (app_dir / "routes").mkdir()
        result = detect_frameworks(
            app_dir, crucible_dir, language="TypeScript",
        )
        assert len(result.missing_frameworks) > 0

    def test_existing_framework_not_missing(self, app_dir: Path, crucible_dir: Path) -> None:
        (app_dir / "package.json").write_text(
            json.dumps({"devDependencies": {"@playwright/test": "^1"}}),
            encoding="utf-8")
        (app_dir / "src").mkdir()
        (app_dir / "src" / "App.tsx").touch()
        (crucible_dir / "package.json").write_text(
            json.dumps({"devDependencies": {"@playwright/test": "^1.40.0"}}),
            encoding="utf-8")
        result = detect_frameworks(
            app_dir, crucible_dir, language="TypeScript",
        )
        assert "playwright" not in result.missing_frameworks

    def test_max_three_frameworks(self, app_dir: Path, crucible_dir: Path) -> None:
        result = detect_frameworks(
            app_dir, crucible_dir, language="TypeScript",
            has_web_server=True,
        )
        assert len(result.recommended_frameworks) <= 3

    def test_default_when_nothing_detected(self, app_dir: Path, crucible_dir: Path) -> None:
        result = detect_frameworks(app_dir, crucible_dir, language="")
        assert len(result.recommended_frameworks) >= 1
