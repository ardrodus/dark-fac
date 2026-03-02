"""Self-onboarding flow for the dark-factory repository.

Detects factory identity, analyzes the project, writes config,
checks required tools, and runs a validation selftest.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


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


def _analyze_project(root: Path) -> dict[str, str]:
    """Detect language and test framework metadata."""
    language = "python"
    framework = "pytest"
    # Confirm pytest markers exist in the repo
    tests_dir = root / "tests"
    has_conftest = (root / "conftest.py").is_file() or (
        tests_dir.is_dir() and (tests_dir / "conftest.py").is_file()
    )
    # Even without conftest, the factory uses pytest conventions
    _ = has_conftest  # informational only; framework stays pytest
    return {"language": language, "framework": framework}


def _check_tools() -> list[tuple[str, bool]]:
    """Check availability of required tools.

    Returns a list of ``(tool_name, found)`` tuples.
    """
    required = ("python", "pytest", "ruff", "mypy", "gh", "git")
    results: list[tuple[str, bool]] = []
    for name in required:
        results.append((name, shutil.which(name) is not None))
    return results


def _write_config(root: Path, metadata: dict[str, str]) -> Path:
    """Write ``.dark-factory/config.json`` with onboarding metadata."""
    import json  # noqa: PLC0415

    config_dir = root / ".dark-factory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    data: dict[str, object] = {
        "self_onboarded": True,
        "language": metadata.get("language", "python"),
        "framework": metadata.get("framework", "pytest"),
        "version": "6.0.0-dev",
    }
    # Merge with existing config if present
    if config_path.is_file():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                existing.update(data)
                data = existing
        except (json.JSONDecodeError, OSError):
            pass  # overwrite on corruption
    config_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return config_path


def _run_selftest_validation() -> bool:
    """Run the built-in selftest and return True if it passes."""
    from factory.cli.handlers import run_selftest  # noqa: PLC0415

    try:
        run_selftest()
        return True
    except SystemExit as exc:
        return exc.code == 0


def run_onboard_self(root: Path | None = None) -> OnboardResult:
    """Execute the full self-onboarding flow.

    Steps:
      1. Detect that this is the factory repo
      2. Analyze project metadata (language, framework)
      3. Check required tools
      4. Write config.json
      5. Run selftest validation gate
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

    # Step 2: Project analysis
    metadata = _analyze_project(root)
    steps.append(
        f"OK: Analysis — language={metadata['language']}, "
        f"framework={metadata['framework']}"
    )

    # Step 3: Tool check
    tool_results = _check_tools()
    missing = [name for name, found in tool_results if not found]
    for name, found in tool_results:
        status = "found" if found else "MISSING"
        steps.append(f"  Tool: {name} — {status}")
    if missing:
        steps.append(f"WARN: Missing tools: {', '.join(missing)}")
    else:
        steps.append("OK: All required tools available")

    # Step 4: Write config
    config_path = _write_config(root, metadata)
    steps.append(f"OK: Config written to {config_path}")

    # Step 5: Validation selftest
    sys.stdout.write("\n--- Running selftest validation ---\n")
    selftest_ok = _run_selftest_validation()
    if selftest_ok:
        steps.append("OK: Selftest validation passed")
    else:
        steps.append("FAIL: Selftest validation failed")
        passed = False

    return OnboardResult(passed=passed, steps=tuple(steps))
