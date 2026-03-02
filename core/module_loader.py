"""Module manifest validator and diagnostic reporter.

Reads ``modules_manifest.yaml`` and provides:
* **validate_manifest** — check for undefined dependencies and cycles
* **format_validation_report** — human-readable validation output
* **format_debug_report** — import all core modules, time them, print results
"""

from __future__ import annotations

import importlib
import logging
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).resolve().parent / "modules_manifest.yaml"


# ── Manifest reading ─────────────────────────────────────────────


def _read_modules(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return the ``modules`` dict from the manifest YAML."""
    raw: dict[str, Any] = yaml.safe_load(
        (path or _MANIFEST_PATH).read_text(encoding="utf-8"),
    )
    return raw.get("modules", {})


# ── Validation ───────────────────────────────────────────────────


def validate_manifest(
    path: Path | None = None,
) -> tuple[bool, tuple[str, ...], int]:
    """Validate the manifest for undefined deps and dependency cycles.

    Returns ``(passed, issues, module_count)``.
    """
    modules = _read_modules(path)
    issues: list[str] = []

    for name, info in modules.items():
        for dep in info.get("dependencies", []):
            if dep not in modules:
                issues.append(
                    f"Module {name!r} depends on {dep!r} which is not registered",
                )

    # DFS cycle detection (white/gray/black colouring)
    white, gray, black = 0, 1, 2
    colour: dict[str, int] = {n: white for n in modules}
    stack: list[str] = []

    def _dfs(node: str) -> None:
        colour[node] = gray
        stack.append(node)
        for dep in modules[node].get("dependencies", []):
            if dep not in modules:
                continue
            if colour[dep] == gray:
                i = stack.index(dep)
                chain = " \u2192 ".join([*stack[i:], dep])
                issues.append(f"Circular dependency: {chain}")
            elif colour[dep] == white:
                _dfs(dep)
        stack.pop()
        colour[node] = black

    for name in modules:
        if colour[name] == white:
            _dfs(name)

    return len(issues) == 0, tuple(issues), len(modules)


# ── Reports ──────────────────────────────────────────────────────


def format_validation_report(
    passed: bool,
    issues: tuple[str, ...],
    module_count: int,
) -> str:
    """Render a human-readable validation report."""
    lines: list[str] = ["Module Manifest Validation", "=" * 60, ""]
    if passed:
        lines.append("  Status  : PASS")
        lines.append(f"  Modules : {module_count}")
    else:
        lines.append(f"  Status  : FAIL \u2014 {len(issues)} issue(s) found")
        lines.append(f"  Modules : {module_count}")
        lines.append("")
        lines.extend(f"  - {issue}" for issue in issues)
    lines.extend(("", "=" * 60))
    return "\n".join(lines)


def format_debug_report(path: Path | None = None) -> str:
    """Import all core modules, time each one, and render a debug report."""
    modules = _read_modules(path)
    core = {n: i for n, i in modules.items() if i.get("load", "deferred") == "core"}
    deferred = [n for n, i in modules.items() if i.get("load", "deferred") == "deferred"]

    log: list[tuple[str, float]] = []
    for name, info in core.items():
        mod_path = str(info.get("path", name))
        start = time.monotonic()
        importlib.import_module(mod_path)
        ms = (time.monotonic() - start) * 1000
        log.append((name, ms))
        logger.debug("Loaded module %r (%.1f ms, startup)", name, ms)

    lines: list[str] = [
        "Module Load Report",
        "=" * 60,
        "",
        f"  Core modules    : {len(core)}",
        f"  Deferred modules: {len(deferred)}",
        "",
        "  Loaded modules:",
    ]
    if log:
        for name, ms in log:
            lines.append(f"    [  startup] {name:<40} {ms:6.1f} ms")
    else:
        lines.append("    (none)")
    lines.extend(("", "  Deferred (not yet loaded):"))
    if deferred:
        lines.extend(f"    {name}" for name in deferred)
    else:
        lines.append("    (all loaded)")
    lines.extend(("", "=" * 60))
    return "\n".join(lines)
