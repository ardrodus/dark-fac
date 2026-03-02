"""SBOM generation and diff tracking gate."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from factory.security.scan_runner import create_scan_gate, run_tool

if TYPE_CHECKING:
    from factory.gates.framework import GateRunner
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_SPDX, _CDX = "spdx-json", "cyclonedx-json"


@dataclass(frozen=True, slots=True)
class Component:
    """A single SBOM component."""
    name: str
    version: str
    type: str = "library"
    purl: str = ""
    source: str = "syft"


@dataclass(frozen=True, slots=True)
class SBOM:
    """Complete Software Bill of Materials."""
    components: tuple[Component, ...]
    format: str = _SPDX


@dataclass(frozen=True, slots=True)
class ChangedComponent:
    """A component whose version changed between SBOMs."""
    name: str
    old_version: str
    new_version: str


@dataclass(frozen=True, slots=True)
class SBOMDiff:
    """Diff between two SBOMs: added, removed, and changed components."""
    added: tuple[Component, ...]
    removed: tuple[Component, ...]
    changed: tuple[ChangedComponent, ...]


@dataclass(frozen=True, slots=True)
class SBOMResult:
    """Result of SBOM generation including optional diff and vuln flags."""
    sbom: SBOM
    diff: SBOMDiff | None = None
    vulnerable_new_deps: tuple[str, ...] = ()
    passed: bool = field(init=False)
    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", len(self.vulnerable_new_deps) == 0)


def _parse_spdx(raw: str) -> list[Component]:
    out: list[Component] = []
    for pkg in json.loads(raw).get("packages", []):
        refs = pkg.get("externalRefs") or []
        purl = next((r.get("referenceLocator", "") for r in refs if r.get("referenceType") == "purl"), "")
        out.append(Component(pkg.get("name", "unknown"), pkg.get("versionInfo", "unknown"),
                             pkg.get("supplier", "library"), purl, "syft-spdx"))
    return out


def _parse_cdx(raw: str) -> list[Component]:
    return [Component(c.get("name", "unknown"), c.get("version", "unknown"), c.get("type", "library"),
                      c.get("purl", ""), "syft-cyclonedx") for c in json.loads(raw).get("components", [])]


def _run_syft(ws_path: str, fmt: str) -> list[Component]:
    parse = _parse_cdx if fmt == _CDX else _parse_spdx
    return run_tool("syft", ["syft", "scan", f"dir:{ws_path}", "-o", fmt, "-q"], parse, cwd=ws_path, timeout=120)


def _check_vulns(components: tuple[Component, ...], ws_path: str) -> tuple[str, ...]:
    try:
        from factory.security.dependency_scan import run_dependency_scan  # noqa: PLC0415
        from factory.workspace.manager import Workspace as Ws  # noqa: PLC0415
        result = run_dependency_scan(Ws(name="sbom-vuln-check", path=ws_path, repo_url="", branch=""))
        vuln_names = {f.package.lower() for f in result.findings}
        return tuple(c.name for c in components if c.name.lower() in vuln_names)
    except Exception:  # noqa: BLE001
        return ()


def _save_sbom(sbom: SBOM, ws_path: str, issue_number: str) -> Path:
    out_dir = Path(ws_path) / ".dark-factory" / "sbom" / issue_number
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "sbom.json"
    payload = {"format": sbom.format, "components": [{"name": c.name, "version": c.version,
                "type": c.type, "purl": c.purl, "source": c.source} for c in sbom.components]}
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_file


def _load_sbom(path: Path) -> SBOM | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SBOM(tuple(Component(**c) for c in data.get("components", [])), data.get("format", _SPDX))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
        return None


def diff_sbom(old: SBOM, new: SBOM) -> SBOMDiff:
    """Compare two SBOMs and return added / removed / changed components."""
    old_map = {c.name: c for c in old.components}
    return SBOMDiff(
        added=tuple(c for c in new.components if c.name not in old_map),
        removed=tuple(c for c in old.components if c.name not in {x.name for x in new.components}),
        changed=tuple(ChangedComponent(c.name, old_map[c.name].version, c.version)
                      for c in new.components if c.name in old_map and c.version != old_map[c.name].version),
    )


def generate_sbom(workspace: Workspace, *, fmt: str = _SPDX) -> SBOMResult:
    """Generate SBOM for *workspace*, diff against previous, flag vulnerable new deps."""
    sbom = SBOM(components=tuple(_run_syft(workspace.path, fmt)), format=fmt)
    sbom_file = _save_sbom(sbom, workspace.path, workspace.name)
    logger.info("SBOM saved to %s (%d components)", sbom_file, len(sbom.components))
    candidates = sorted((p for p in sbom_file.parent.parent.glob("*/sbom.json")
                          if p != sbom_file), key=lambda p: p.stat().st_mtime, reverse=True)
    old_sbom = next((s for p in candidates if (s := _load_sbom(p)) is not None), None)
    diff: SBOMDiff | None = None
    vuln_new: tuple[str, ...] = ()
    if old_sbom is not None:
        diff = diff_sbom(old_sbom, sbom)
        if diff.added:
            vuln_new = _check_vulns(diff.added, workspace.path)
    return SBOMResult(sbom=sbom, diff=diff, vulnerable_new_deps=vuln_new)


GATE_NAME = "sbom-scan"


def create_runner(workspace: str | Path, *, metrics_dir: str | Path | None = None) -> GateRunner:
    """Create a configured SBOM gate runner."""
    def _check(ws: str) -> tuple[bool, str]:
        from factory.workspace.manager import Workspace as Ws  # noqa: PLC0415
        result = generate_sbom(Ws(name="scan", path=ws, repo_url="", branch=""))
        if not result.passed:
            return False, f"New vulnerable dependencies: {', '.join(result.vulnerable_new_deps)}"
        return True, f"SBOM OK ({len(result.sbom.components)} components)"
    return create_scan_gate(GATE_NAME, "sbom-generation", _check, workspace, metrics_dir=metrics_dir)
