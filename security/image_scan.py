"""Container image scan gate — scan built images for OS-level vulnerabilities."""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dark_factory.gates.framework import GateRunner
from dark_factory.security.scan_runner import run_tool

logger = logging.getLogger(__name__)
_CRIT, _HIGH, _MED = 9.0, 7.0, 4.0
_SEV_FALLBACK = {"CRITICAL": 9.5, "HIGH": 7.5, "MEDIUM": 5.0, "Critical": 9.5, "High": 7.5, "Medium": 5.0,
                 "critical": 9.5, "high": 7.5, "medium": 5.0}


def _classify(cvss: float) -> str:
    return "critical" if cvss >= _CRIT else "high" if cvss >= _HIGH else "medium" if cvss >= _MED else "low"


@dataclass(frozen=True, slots=True)
class Finding:
    """A single container-image vulnerability."""
    package: str
    version: str
    cve: str
    cvss: float
    severity: str = field(init=False)
    fix_version: str = ""
    def __post_init__(self) -> None:
        object.__setattr__(self, "severity", _classify(self.cvss))


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Aggregated image scan result."""
    findings: tuple[Finding, ...]
    image_tag: str
    scanner_used: str
    passed: bool = field(init=False)
    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", not any(f.cvss >= _CRIT for f in self.findings))


def _parse_trivy(raw: str) -> list[Finding]:
    findings: list[Finding] = []
    for res in json.loads(raw).get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            score = max((float(s.get("V3Score") or s.get("V2Score") or 0)
                         for s in (v.get("CVSS") or {}).values()), default=0.0)
            if score == 0.0:
                score = _SEV_FALLBACK.get((v.get("Severity") or "").upper(), 2.0)
            findings.append(Finding(v.get("PkgName", "unknown"), v.get("InstalledVersion", "unknown"),
                                    v.get("VulnerabilityID", ""), score, fix_version=v.get("FixedVersion") or "no fix"))
    return findings


def _parse_grype(raw: str) -> list[Finding]:
    findings: list[Finding] = []
    for m in json.loads(raw).get("matches") or []:
        vuln, art = m.get("vulnerability") or {}, m.get("artifact") or {}
        score = max((float(c.get("metrics", {}).get("baseScore", 0)) for c in vuln.get("cvss") or []), default=0.0)
        if score == 0.0:
            score = _SEV_FALLBACK.get(vuln.get("severity", ""), 2.0)
        fix = (vuln.get("fix") or {}).get("versions", ["no fix"])
        findings.append(Finding(art.get("name", "unknown"), art.get("version", "unknown"),
                                vuln.get("id", ""), score, fix_version=fix[0] if fix else "no fix"))
    return findings


def _parse_docker_scout(raw: str) -> list[Finding]:
    findings: list[Finding] = []
    for v in json.loads(raw).get("vulnerabilities") or []:
        score = float(v.get("cvss_score", 0) or 0)
        if score == 0.0:
            score = _SEV_FALLBACK.get((v.get("severity") or "").lower(), 2.0)
        findings.append(Finding(v.get("package", "unknown"), v.get("version", "unknown"),
                                v.get("cve", ""), score, fix_version=v.get("fix_version") or "no fix"))
    return findings


_TOOLS: list[tuple[str, list[str]]] = [
    ("trivy", ["trivy", "image", "--format", "json", "--quiet"]),
    ("grype", ["grype", "-o", "json"]),
    ("docker", ["docker", "scout", "cves", "--format", "json"]),
]
_PARSERS = {"trivy": _parse_trivy, "grype": _parse_grype, "docker": _parse_docker_scout}


def run_image_scan(image_tag: str, *, timeout: int = 180) -> ScanResult:
    """Scan a container image for OS-level vulnerabilities."""
    for tool, base_cmd in _TOOLS:
        if not shutil.which(tool):
            continue
        logger.info("Scanning image %s with %s", image_tag, tool)
        cmd = [*base_cmd, image_tag] if tool != "grype" else [base_cmd[0], image_tag, *base_cmd[1:]]
        findings = run_tool(tool, cmd, _PARSERS[tool], timeout=timeout)
        return ScanResult(findings=tuple(findings), image_tag=image_tag, scanner_used=tool)
    logger.warning("No image scanner found (trivy, grype, docker scout)")
    return ScanResult(findings=(), image_tag=image_tag, scanner_used="none")


GATE_NAME = "image-scan"


def create_runner(image_tag: str, *, metrics_dir: str | Path | None = None) -> GateRunner:
    """Create a configured image-scan gate runner."""
    runner = GateRunner(GATE_NAME, metrics_dir=metrics_dir)

    def _check_image() -> bool | str:
        sr = run_image_scan(image_tag)
        crit = sum(1 for f in sr.findings if f.severity == "critical")
        if crit > 0:
            raise RuntimeError(f"Critical image vulnerabilities: {crit} blocking")
        warns = sum(1 for f in sr.findings if f.severity == "high")
        return f"image scan OK ({len(sr.findings)} finding(s), {warns} high, {crit} critical)"

    runner.register_check("image-vuln-scan", _check_image)
    return runner
