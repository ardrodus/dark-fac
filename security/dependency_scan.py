"""Dependency vulnerability scan — CVSS >= 9.0 blocks; >= 7.0 warns."""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from factory.gates.framework import GateRunner
from factory.integrations.shell import run_command

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Finding:
    """A single dependency vulnerability."""
    package: str
    version: str
    vulnerability: str
    cvss: float
    severity: str = field(init=False)
    def __post_init__(self) -> None:
        sev = "critical" if self.cvss >= 9.0 else "high" if self.cvss >= 7.0 else "medium" if self.cvss >= 4.0 else "low"  # noqa: E501, PLR2004
        object.__setattr__(self, "severity", sev)


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Aggregated dependency scan result."""
    findings: tuple[Finding, ...]
    passed: bool = field(init=False)
    blocked_count: int = field(init=False)
    warning_count: int = field(init=False)
    def __post_init__(self) -> None:
        blocked = sum(1 for f in self.findings if f.cvss >= 9.0)
        warns = sum(1 for f in self.findings if 7.0 <= f.cvss < 9.0)
        object.__setattr__(self, "blocked_count", blocked)
        object.__setattr__(self, "warning_count", warns)
        object.__setattr__(self, "passed", blocked == 0)


_LANG_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("nodejs", ("package-lock.json", "yarn.lock", "package.json")),
    ("python", ("requirements.txt", "Pipfile.lock", "pyproject.toml", "setup.py")),
    ("rust", ("Cargo.lock", "Cargo.toml")), ("go", ("go.sum", "go.mod")),
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts")),
    ("ruby", ("Gemfile.lock", "Gemfile")),
]
_DOTNET_GLOBS = [f"{'*/' * d}*.{e}" for d in range(4) for e in ("csproj", "sln")]


def _detect_languages(ws: Path) -> list[str]:
    found = [t for t, ms in _LANG_MARKERS if any((ws / m).is_file() for m in ms)]
    if any(f for pat in _DOTNET_GLOBS for f in ws.glob(pat)):
        found.append("dotnet")
    return found


def _scan(tool: str, cmd: list[str], ws: str, parse: Callable[[str], list[Finding]], *, timeout: int = 120) -> list[Finding]:  # noqa: E501
    if not shutil.which(tool):
        return []
    raw = run_command(cmd, cwd=ws, timeout=timeout).stdout.strip()
    if not raw:
        return []
    try:
        return parse(raw)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
        logger.warning("Failed to parse %s output", tool)
        return []


def _parse_nodejs(raw: str) -> list[Finding]:
    fs: list[Finding] = []
    for name, v in (json.loads(raw).get("vulnerabilities") or {}).items():
        cvss = {"critical": 9.5, "high": 7.5, "moderate": 5.0}.get(v.get("severity", ""), 2.0)
        via = v.get("via") or []
        cve = (via[0].get("url", "") if isinstance(via[0], dict) else str(via[0])) if via else ""
        fs.append(Finding(name, v.get("range", "unknown"), cve, cvss))
    return fs


def _parse_python(raw: str) -> list[Finding]:
    return [Finding(d["name"], d.get("version", "unknown"), v.get("id", ""), 7.0 if v.get("fix_versions") else 5.0)
            for d in json.loads(raw).get("dependencies", []) for v in d.get("vulns", [])]


def _parse_rust(raw: str) -> list[Finding]:
    fs: list[Finding] = []
    for v in json.loads(raw).get("vulnerabilities", {}).get("list", []):
        adv = v.get("advisory", {})
        cve = next((a for a in adv.get("aliases", []) if a.startswith("CVE")), adv.get("id", ""))
        patched = (v.get("versions", {}).get("patched") or ["unknown"])[0]
        fs.append(Finding(adv.get("package", "unknown"), patched, cve, float(adv.get("cvss", 5.0))))
    return fs


def _parse_go(raw: str) -> list[Finding]:
    fs: list[Finding] = []
    for line in raw.splitlines():
        vuln = (json.loads(line) if line.strip() else {}).get("vulnerability")
        if not vuln:
            continue
        mod, aliases = (vuln.get("modules") or [{}])[0], vuln.get("aliases", [])
        cve = next((a for a in aliases if a.startswith("CVE")), vuln.get("id", ""))
        fs.append(Finding(mod.get("path", "unknown"), mod.get("found_version", "unknown"), cve, 7.0))
    return fs


def _parse_java(raw: str) -> list[Finding]:
    return [Finding(d.get("fileName", "unknown"), d.get("version", "unknown"), v.get("name", ""),
                    float(v.get("cvssv3", {}).get("baseScore", v.get("cvssv2", {}).get("score", 5.0))))
            for d in json.loads(raw).get("dependencies", []) for v in d.get("vulnerabilities", [])]


def _scan_java(ws: str) -> list[Finding]:
    if not shutil.which("dependency-check"):
        return []
    rpt = Path(ws) / ".dark-factory" / "security" / "owasp-dc-raw.json"
    rpt.parent.mkdir(parents=True, exist_ok=True)
    run_command(["dependency-check", "--scan", ".", "--format", "JSON",
                 "--out", str(rpt), "--disableAssembly"], cwd=ws, timeout=300)
    try:
        return _parse_java(rpt.read_text(encoding="utf-8")) if rpt.is_file() else []
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
        logger.warning("Failed to parse OWASP dependency-check output")
        return []


def _parse_dotnet(raw: str) -> list[Finding]:
    sev_map = {"critical": 9.5, "high": 7.5, "moderate": 5.0}
    fs: list[Finding] = []
    for pj in json.loads(raw).get("projects", []):
        for fw in pj.get("frameworks", []):
            for pkg in fw.get("topLevelPackages", []):
                for v in pkg.get("vulnerabilities", []):
                    cvss = sev_map.get((v.get("severity") or "medium").lower(), 2.0)
                    fs.append(Finding(pkg["id"], pkg.get("resolvedVersion", "unknown"), v.get("advisoryurl", ""), cvss))
    return fs


def _parse_ruby(raw: str) -> list[Finding]:
    fs: list[Finding] = []
    for r in json.loads(raw).get("results", []):
        gem, adv = r.get("gem", {}), r.get("advisory", {})
        cvss = float(adv.get("cvss_v3", adv.get("cvss_v2", 5.0)))
        fs.append(Finding(gem.get("name", "unknown"), gem.get("version", "unknown"), adv.get("cve", ""), cvss))
    return fs


_SCANNERS: dict[str, Callable[[str], list[Finding]]] = {
    "nodejs": lambda w: _scan("npm", ["npm", "audit", "--json"], w, _parse_nodejs),
    "python": lambda w: _scan("pip-audit", ["pip-audit", "--format=json"], w, _parse_python),
    "rust": lambda w: _scan("cargo", ["cargo", "audit", "--json"], w, _parse_rust),
    "go": lambda w: _scan("govulncheck", ["govulncheck", "-json", "./..."], w, _parse_go),
    "java": _scan_java,
    "dotnet": lambda w: _scan("dotnet", ["dotnet", "list", "package", "--vulnerable", "--format", "json"], w, _parse_dotnet),  # noqa: E501
    "ruby": lambda w: _scan("bundle-audit", ["bundle-audit", "check", "--format", "json"], w, _parse_ruby),
}


def run_dependency_scan(workspace: Workspace) -> ScanResult:
    """Scan workspace dependencies for known vulnerabilities."""
    languages = _detect_languages(Path(workspace.path))
    if not languages:
        return ScanResult(findings=())
    findings: list[Finding] = []
    for lang in languages:
        if scanner := _SCANNERS.get(lang):
            findings.extend(scanner(workspace.path))
    return ScanResult(findings=tuple(findings))


GATE_NAME = "dependency-scan"


def create_runner(workspace: str | Path, *, metrics_dir: str | Path | None = None) -> GateRunner:
    """Create a configured dependency-scan gate runner."""
    ws = str(workspace)
    runner = GateRunner(GATE_NAME, metrics_dir=metrics_dir)
    def _check_deps() -> bool | str:
        from factory.workspace.manager import Workspace as Ws  # noqa: PLC0415
        result = run_dependency_scan(Ws(name="scan", path=ws, repo_url="", branch=""))
        if result.blocked_count > 0:
            msg = f"Critical vulnerabilities: {result.blocked_count} blocking"
            raise RuntimeError(msg)
        return f"dep scan OK ({len(result.findings)} finding(s), {result.blocked_count} blocking)"
    runner.register_check("dep-vuln-scan", _check_deps)
    return runner
