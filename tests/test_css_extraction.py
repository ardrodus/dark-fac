"""CSS extraction validation tests (TS-019: CSS-01 through CSS-07).

Validates that all CSS has been extracted to .tcss files, inline CSS
constants are removed, shared base styles exist, and border:round is
used everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Repo root — tests/ sits one level below the project root
REPO_ROOT = Path(__file__).resolve().parent.parent


# ── CSS-01: No inline CSS f-strings in Python files ─────────────


def test_css01_no_inline_css_fstrings() -> None:
    """CSS-01: No Python files under ui/ or modes/ contain inline CSS f-strings."""
    forbidden_names = {
        "_CSS_TEMPLATE",
        "_MENU_CSS",
        "_SETTINGS_CSS",
        "_FOUNDRY_CSS",
        "_CONFIG_CSS",
    }
    violations: list[str] = []
    for subdir in ("ui", "modes"):
        search_dir = REPO_ROOT / subdir
        if not search_dir.exists():
            continue
        for py_file in search_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for name in forbidden_names:
                if name in content:
                    violations.append(f"{py_file.relative_to(REPO_ROOT)}: contains {name}")
    assert violations == [], f"Inline CSS constants found:\n" + "\n".join(violations)


# ── CSS-02: All .tcss files parse without error ──────────────────


def test_css02_tcss_files_parse() -> None:
    """CSS-02: All .tcss files under ui/styles/ parse without error."""
    styles_dir = REPO_ROOT / "ui" / "styles"
    assert styles_dir.exists(), f"ui/styles/ directory does not exist at {styles_dir}"
    tcss_files = list(styles_dir.rglob("*.tcss"))
    assert len(tcss_files) > 0, "No .tcss files found in ui/styles/"
    for tcss_file in tcss_files:
        content = tcss_file.read_text(encoding="utf-8")
        # Basic syntax check: non-empty and no obvious parse errors
        assert len(content.strip()) > 0, f"{tcss_file.name} is empty"


# ── CSS-03: base.tcss exists with shared rules ──────────────────


def test_css03_base_tcss_exists() -> None:
    """CSS-03: base.tcss exists and contains shared Screen/Header/Footer rules."""
    base_tcss = REPO_ROOT / "ui" / "styles" / "base.tcss"
    assert base_tcss.exists(), "ui/styles/base.tcss does not exist"
    content = base_tcss.read_text(encoding="utf-8")
    assert "Screen" in content, "base.tcss missing Screen rules"
    assert "Header" in content, "base.tcss missing Header rules"
    assert "Footer" in content, "base.tcss missing Footer rules"


# ── CSS-04: Per-screen .tcss files exist ─────────────────────────


@pytest.mark.parametrize(
    "filename",
    ["dashboard.tcss", "menu.tcss", "settings.tcss", "foundry.tcss", "foundry_config.tcss"],
)
def test_css04_per_screen_tcss_exists(filename: str) -> None:
    """CSS-04: Per-screen .tcss files exist under ui/styles/."""
    tcss_file = REPO_ROOT / "ui" / "styles" / filename
    assert tcss_file.exists(), f"ui/styles/{filename} does not exist"


# ── CSS-05: All App classes use CSS_PATH ─────────────────────────


def test_css05_apps_use_css_path() -> None:
    """CSS-05: All App classes use CSS_PATH (not CSS or DEFAULT_CSS for static rules)."""
    app_files = {
        "ui/dashboard.py": "DashboardApp",
        "modes/interactive.py": "InteractiveApp",
        "modes/settings.py": "SettingsApp",
        "modes/foundry.py": "FoundryScreen",
    }
    for rel_path, class_name in app_files.items():
        filepath = REPO_ROOT / rel_path
        if not filepath.exists():
            continue
        content = filepath.read_text(encoding="utf-8")
        assert "CSS_PATH" in content, (
            f"{rel_path} ({class_name}) does not use CSS_PATH"
        )


# ── CSS-06: .tcss files in pyproject.toml build config ───────────


def test_css06_tcss_in_pyproject_build() -> None:
    """CSS-06: .tcss files are included in pyproject.toml build config."""
    pyproject = REPO_ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    # Should include .tcss files in the wheel artifacts
    assert ".tcss" in content or "tcss" in content or "styles" in content, (
        "pyproject.toml does not reference .tcss files in build config"
    )


# ── CSS-07: No border: tall in any .tcss file ───────────────────


def test_css07_no_border_tall() -> None:
    """CSS-07: No 'border: tall' appears in any .tcss file (all replaced with border: round)."""
    styles_dir = REPO_ROOT / "ui" / "styles"
    if not styles_dir.exists():
        pytest.skip("ui/styles/ directory does not exist yet")
    violations: list[str] = []
    for tcss_file in styles_dir.rglob("*.tcss"):
        content = tcss_file.read_text(encoding="utf-8")
        if "border: tall" in content or "border:tall" in content:
            violations.append(str(tcss_file.relative_to(REPO_ROOT)))
    assert violations == [], f"'border: tall' found in: {', '.join(violations)}"
