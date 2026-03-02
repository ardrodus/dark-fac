"""Sentinel security orchestrator — sequences all 6 security gates, produces a unified verdict."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.security.config import SecurityConfig, is_excepted

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_MODE_THRESHOLD: dict[str, int] = {"strict": 2, "standard": 3, "audit": 999}


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of a single security gate."""
    gate: str
    passed: bool
    findings_count: int
    summary: str = ""
    raw: Any = None


@dataclass(frozen=True, slots=True)
class SentinelVerdict:
    """Unified security verdict produced by the Sentinel orchestrator."""
    passed: bool
    gate_results: dict[str, GateResult]
    blocking_findings: tuple[dict[str, str], ...]
    advisory_findings: tuple[dict[str, str], ...]
    mode: str = "standard"
    summary: str = ""


def _has_dockerfile(workspace_path: str) -> bool:
    ws = Path(workspace_path)
    return any(ws.glob("Dockerfile*")) or any(ws.glob("**/Dockerfile*"))


def _fd(gate: str, sev: str, desc: str, file: str = "") -> dict[str, str]:
    return {"gate": gate, "severity": sev, "description": desc, "file": file}


def _is_blocking(severity: str, mode: str) -> bool:
    return _SEV_RANK.get(severity, 0) >= _MODE_THRESHOLD.get(mode, 3)


def _apply_exceptions(
    findings: list[dict[str, str]], config: SecurityConfig,
) -> list[dict[str, str]]:
    """Return findings not covered by an active security exception."""
    kept = [f for f in findings if not is_excepted(config, f.get("description", ""), f.get("file", ""))]
    removed = len(findings) - len(kept)
    if removed:
        logger.info("Sentinel: %d finding(s) excepted via security-exceptions", removed)
    return kept


def _save_results(verdict: SentinelVerdict, workspace_path: str, issue_number: str) -> Path:
    out_dir = Path(workspace_path) / ".dark-factory" / "security" / issue_number
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "sentinel-verdict.json"
    payload = {
        "passed": verdict.passed, "mode": verdict.mode, "summary": verdict.summary,
        "gate_results": {
            n: {"gate": g.gate, "passed": g.passed, "findings_count": g.findings_count, "summary": g.summary}
            for n, g in verdict.gate_results.items()
        },
        "blocking_findings": list(verdict.blocking_findings),
        "advisory_findings": list(verdict.advisory_findings),
    }
    out_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("Sentinel verdict saved to %s", out_file)
    return out_file


def _gr(gate: str, passed: bool, count: int, summary: str, raw: Any = None) -> GateResult:
    return GateResult(gate=gate, passed=passed, findings_count=count, summary=summary, raw=raw)


def run_sentinel(  # noqa: PLR0912, PLR0915
    workspace: Workspace, diff: str, config: SecurityConfig,
    *, invoke_fn: Callable[[str], str] | None = None,
) -> SentinelVerdict:
    """Sequence all security gates and produce a unified verdict.

    Gate order: secret scan -> dependency scan -> SAST -> image scan
    (if Dockerfile present) -> SBOM -> AI review.
    """
    from factory.security.ai_security_review import run_security_review  # noqa: PLC0415
    from factory.security.dependency_scan import run_dependency_scan  # noqa: PLC0415
    from factory.security.image_scan import run_image_scan  # noqa: PLC0415
    from factory.security.sast_scan import run_sast_scan  # noqa: PLC0415
    from factory.security.sbom_scan import generate_sbom  # noqa: PLC0415
    from factory.security.secret_scan import run_secret_scan  # noqa: PLC0415

    mode = config.mode
    gates: dict[str, GateResult] = {}
    findings: list[dict[str, str]] = []
    prior: list[Any] = []

    # 1. Secret scan
    logger.info("Sentinel [1/6]: secret scan")
    sr = run_secret_scan(workspace, diff)
    gates["secret-scan"] = _gr("secret-scan", sr.passed, len(sr.findings), f"{len(sr.findings)} finding(s)", sr)
    for sf in sr.findings:
        findings.append(_fd("secret-scan", sf.severity, sf.rule, sf.file))
    prior.append(sr)

    # 2. Dependency scan
    logger.info("Sentinel [2/6]: dependency scan")
    dr = run_dependency_scan(workspace)
    gates["dependency-scan"] = _gr(
        "dependency-scan", dr.passed, len(dr.findings),
        f"{dr.blocked_count} blocking, {dr.warning_count} warnings", dr,
    )
    for df in dr.findings:
        findings.append(_fd("dependency-scan", df.severity, df.vulnerability, df.package))
    prior.append(dr)

    # 3. SAST
    logger.info("Sentinel [3/6]: SAST scan")
    sa = run_sast_scan(workspace, diff, mode=mode, invoke_fn=invoke_fn)
    eff = sa.layer2_findings or sa.layer1_findings
    gates["sast-scan"] = _gr(
        "sast-scan", sa.passed, len(eff),
        f"L1={len(sa.layer1_findings)}, L2={len(sa.layer2_findings)}, FP={sa.false_positives_removed}", sa,
    )
    for ef in eff:
        if not ef.false_positive:
            findings.append(_fd("sast-scan", ef.severity, ef.description, ef.file))
    prior.append(sa)

    # 4. Image scan (if applicable)
    if _has_dockerfile(workspace.path):
        logger.info("Sentinel [4/6]: image scan")
        ir = run_image_scan(f"{workspace.name}:latest")
        gates["image-scan"] = _gr(
            "image-scan", ir.passed, len(ir.findings),
            f"scanner={ir.scanner_used}, {len(ir.findings)} finding(s)", ir,
        )
        for imf in ir.findings:
            findings.append(_fd("image-scan", imf.severity, imf.cve, imf.package))
        prior.append(ir)
    else:
        logger.info("Sentinel [4/6]: image scan — skipped (no Dockerfile)")
        gates["image-scan"] = _gr("image-scan", True, 0, "skipped (no Dockerfile)")

    # 5. SBOM
    logger.info("Sentinel [5/6]: SBOM generation")
    sb = generate_sbom(workspace)
    gates["sbom-scan"] = _gr(
        "sbom-scan", sb.passed, len(sb.vulnerable_new_deps),
        f"{len(sb.sbom.components)} components, {len(sb.vulnerable_new_deps)} vulnerable new deps", sb,
    )
    for dep in sb.vulnerable_new_deps:
        findings.append(_fd("sbom-scan", "high", f"vulnerable new dep: {dep}", dep))
    prior.append(sb)

    # 6. AI security review
    logger.info("Sentinel [6/6]: AI security review")
    rv = run_security_review(workspace, diff, prior, invoke_fn=invoke_fn)
    gates["ai-review"] = _gr(
        "ai-review", rv.passed, len(rv.findings),
        f"verdict={rv.verdict}, {len(rv.findings)} finding(s)", rv,
    )
    for rf in rv.findings:
        findings.append(_fd("ai-review", rf.severity, rf.description, rf.file))

    # Apply exceptions, classify blocking vs advisory
    active = _apply_exceptions(findings, config)
    blocking = [f for f in active if _is_blocking(f["severity"], mode)]
    advisory = [f for f in active if not _is_blocking(f["severity"], mode)]
    passed = len(blocking) == 0
    summary = f"Sentinel {mode}: {'PASSED' if passed else 'FAILED'} — {len(blocking)} blocking, {len(advisory)} advisory"
    logger.info(summary)

    verdict = SentinelVerdict(
        passed=passed, gate_results=gates,
        blocking_findings=tuple(blocking), advisory_findings=tuple(advisory),
        mode=mode, summary=summary,
    )
    _save_results(verdict, workspace.path, workspace.name)
    return verdict
