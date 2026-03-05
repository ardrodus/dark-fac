"""Self-onboarding flow for the dark-factory repository.

Detects factory identity, analyzes with factory-specific overrides,
writes config, checks required tools, and runs a validation selftest.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dark_factory.setup.project_analyzer import AnalysisResult


@dataclass(frozen=True, slots=True)
class OnboardResult:
    """Result of the self-onboarding flow."""

    passed: bool
    steps: tuple[str, ...]  # human-readable log of each step


def _detect_factory_repo(root: Path) -> bool:
    """Return True if *root* looks like the dark-factory repo."""
    markers = (
        root / "__init__.py",
        root / "cli" / "parser.py",
        root / "cli" / "dispatch.py",
        root / "gates" / "framework.py",
        root / "__main__.py",
    )
    return all(m.is_file() for m in markers)


def _analyze_self(_root: Path) -> AnalysisResult:
    """Return hardcoded analysis for the factory repo itself.

    No need to run the analysis pipeline -- we know what we are.
    """
    from dark_factory.setup.project_analyzer import AnalysisResult as AR  # noqa: PLC0415

    return AR(
        language="Python",
        framework="pytest",
        detected_app_type="console",
        confidence="high",
        description="Python/pytest project (dark-factory)",
        test_cmd="pytest",
        required_tools=("python", "pip", "pytest", "ruff", "mypy"),
        source_dirs=("dark_factory/",),
        test_dirs=("tests/",),
    )


def _write_config(root: Path, analysis: AnalysisResult) -> Path:
    """Write ``.dark-factory/config.json`` with self-onboarding metadata."""
    from dark_factory.core.config_manager import load_config, save_config  # noqa: PLC0415
    from dark_factory.setup.config_init import add_repo_to_config, init_config  # noqa: PLC0415

    config_path = init_config(start=root)
    add_repo_to_config(
        repo=str(root),
        app_type="console",
        analysis=analysis,
        start=root,
    )
    # Mark the repo entry with self-onboarding flags
    cfg = load_config(root)
    repos = cfg.data.get("repos", [])
    if isinstance(repos, list):
        for entry in repos:
            if isinstance(entry, dict) and entry.get("name") == str(root):
                entry["is_self"] = True
                entry["self_onboarded"] = True
    cfg.data["self_onboarded"] = True
    save_config(cfg)
    return config_path


def _check_tools() -> list[tuple[str, bool]]:
    """Check availability of required tools."""
    required = ("python", "pytest", "ruff", "mypy", "gh", "git")
    return [(name, shutil.which(name) is not None) for name in required]


def _run_selftest_validation() -> bool:
    """Run the built-in selftest and return True if it passes."""
    from dark_factory.cli.handlers import run_selftest  # noqa: PLC0415

    try:
        run_selftest()
        return True
    except SystemExit as exc:
        return exc.code == 0


def run_onboard_self(root: Path | None = None) -> OnboardResult:
    """Execute the full self-onboarding flow.

    Steps:
      1. Self-detect -- verify this is the factory repo
      2. Self-analyze -- run project analyzer with factory overrides
      3. Write config -- .dark-factory/config.json with is_self=true
      4. Tool check -- verify required tools are installed
      5. Validation -- run selftest as go/no-go gate
    """
    if root is None:
        root = Path(__file__).resolve().parent.parent
    steps: list[str] = []
    passed = True

    # Step 1: Self-detection
    if not _detect_factory_repo(root):
        steps.append("FAIL: Not the dark-factory repository")
        return OnboardResult(passed=False, steps=tuple(steps))
    steps.append("OK: Factory repository detected")

    # Step 2: Self-analysis with factory-specific overrides
    analysis = _analyze_self(root)
    steps.append(
        f"OK: Analysis -- language={analysis.language}, "
        f"framework={analysis.framework}, app_type={analysis.detected_app_type}"
    )

    # Step 3: Write config
    config_path = _write_config(root, analysis)
    steps.append(f"OK: Config written to {config_path}")

    # Step 4: Tool check
    tool_results = _check_tools()
    missing = [name for name, found in tool_results if not found]
    for name, found in tool_results:
        status = "found" if found else "MISSING"
        steps.append(f"  Tool: {name} -- {status}")
    if missing:
        steps.append(f"WARN: Missing tools: {', '.join(missing)}")
    else:
        steps.append("OK: All required tools available")

    # Step 5: Validation selftest
    sys.stdout.write("\n--- Running selftest validation ---\n")
    selftest_ok = _run_selftest_validation()
    if selftest_ok:
        steps.append("OK: Selftest validation passed")
    else:
        steps.append("FAIL: Selftest validation failed")
        passed = False

    return OnboardResult(passed=passed, steps=tuple(steps))
