"""File size compliance: warn at 300 lines, hard fail at 500 lines."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_FACTORY_ROOT = Path(__file__).resolve().parents[1]
WARN_THRESHOLD, FAIL_THRESHOLD = 300, 500
EXEMPT: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class FileSizeResult:
    passed: bool
    warnings: tuple[str, ...]
    failures: tuple[str, ...]
    scanned: int


def validate(root: Path | None = None) -> FileSizeResult:
    """Check all Python files under *root* for size compliance."""
    base = root or _FACTORY_ROOT
    warns: list[str] = []
    fails: list[str] = []
    count = 0
    for py in sorted(base.rglob("*.py")):
        rel = str(py.relative_to(base.parent))
        if rel in EXEMPT:
            continue
        n = len(py.read_text(encoding="utf-8").splitlines())
        count += 1
        if n > FAIL_THRESHOLD:
            fails.append(f"{rel}: {n} lines (> {FAIL_THRESHOLD})")
        elif n > WARN_THRESHOLD:
            warns.append(f"{rel}: {n} lines (> {WARN_THRESHOLD})")
    return FileSizeResult(passed=not fails, warnings=tuple(warns),
                          failures=tuple(fails), scanned=count)


def format_report(result: FileSizeResult) -> str:
    """Render a human-readable file size report."""
    hdr = ["File Size Compliance", "=" * 60,
           f"  Status : {'PASS' if result.passed else 'FAIL'}",
           f"  Scanned: {result.scanned} files"]
    hdr.extend(f"  [WARN] {w}" for w in result.warnings)
    hdr.extend(f"  [FAIL] {f}" for f in result.failures)
    hdr.append("=" * 60)
    return "\n".join(hdr)
