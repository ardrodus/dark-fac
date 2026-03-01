"""Load and report on the bash→Python migration manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ModuleEntry:
    """A single module record from the migration manifest."""

    name: str
    lang: str
    status: str
    description: str


_MANIFEST_PATH = Path(__file__).resolve().parent / "migration_manifest.yaml"


def load_manifest(path: Path | None = None) -> list[ModuleEntry]:
    """Parse *migration_manifest.yaml* and return a list of :class:`ModuleEntry`.

    Parameters
    ----------
    path:
        Override the manifest location (useful for testing).
    """
    manifest_path = path or _MANIFEST_PATH
    raw: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    modules_raw: dict[str, Any] = raw.get("modules", {})
    entries: list[ModuleEntry] = []
    for name, info in modules_raw.items():
        lang = str(info.get("lang", "bash"))
        status = str(info.get("status", "pending"))
        description = str(info.get("description", ""))
        entries.append(ModuleEntry(name=name, lang=lang, status=status, description=description))
    return entries


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """Summary statistics for the migration."""

    total: int
    migrated: int
    verified: int
    in_progress: int
    pending: int
    entries: tuple[ModuleEntry, ...]


def migration_report(path: Path | None = None) -> MigrationReport:
    """Build a :class:`MigrationReport` from the manifest."""
    entries = load_manifest(path)
    return MigrationReport(
        total=len(entries),
        migrated=sum(1 for e in entries if e.status in ("migrated", "verified")),
        verified=sum(1 for e in entries if e.status == "verified"),
        in_progress=sum(1 for e in entries if e.status == "in_progress"),
        pending=sum(1 for e in entries if e.status == "pending"),
        entries=tuple(entries),
    )


def format_report(report: MigrationReport) -> str:
    """Render a human-readable migration progress report."""
    lines: list[str] = []
    lines.append("Migration Progress")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Total modules : {report.total}")
    lines.append(f"  Migrated      : {report.migrated} (includes verified)")
    lines.append(f"  Verified      : {report.verified}")
    lines.append(f"  In progress   : {report.in_progress}")
    lines.append(f"  Pending       : {report.pending}")
    pct = (report.migrated / report.total * 100) if report.total else 0
    lines.append(f"  Progress      : {pct:.0f}%")
    lines.append("")
    lines.append("-" * 60)
    lines.append(f"{'Module':<40} {'Lang':<8} {'Status'}")
    lines.append("-" * 60)
    for entry in report.entries:
        lines.append(f"{entry.name:<40} {entry.lang:<8} {entry.status}")
    lines.append("-" * 60)
    return "\n".join(lines)
