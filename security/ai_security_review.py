"""AI-powered security review gate — Claude-driven diff-focused security analysis."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.security.scan_runner import create_scan_gate

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.gates.framework import GateRunner
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT = 300
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_OWASP_CATEGORIES = (
    "injection", "broken-auth", "sensitive-data", "xxe", "broken-access",
    "misconfiguration", "xss", "insecure-deserialization", "vuln-components",
    "insufficient-logging",
)


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    """A single AI-identified security finding."""
    category: str
    severity: str  # critical, high, medium, low, info
    description: str
    file: str = ""
    line: int = 0
    cwe: str = ""
    attack_vector: str = ""
    recommendation: str = ""
    confidence: str = "medium"


@dataclass(frozen=True, slots=True)
class SecurityReviewResult:
    """Outcome of an AI security review."""
    verdict: str  # PASS, FAIL, CONDITIONAL
    findings: tuple[SecurityFinding, ...]
    recommendations: tuple[str, ...]
    summary: str = ""
    raw_output: str = ""
    passed: bool = field(init=False)
    def __post_init__(self) -> None:
        v = self.verdict.upper()
        object.__setattr__(self, "passed", v == "PASS")


def _format_scan_context(scan_results: list[Any]) -> str:
    """Summarize prior scan results so the AI avoids re-flagging known issues."""
    if not scan_results:
        return "No prior scan results."
    parts: list[str] = []
    for i, sr in enumerate(scan_results, 1):
        name = getattr(sr, "GATE_NAME", None) or type(sr).__name__
        passed = getattr(sr, "passed", None)
        raw_f = getattr(sr, "findings", None) or getattr(sr, "layer1_findings", ())
        findings: tuple[object, ...] = tuple(raw_f) if raw_f else ()
        count = len(findings)
        parts.append(f"{i}. {name}: {'PASS' if passed else 'FAIL'} ({count} finding(s))")
        for f in findings[:5]:
            desc = getattr(f, "description", None) or getattr(f, "rule", str(f))
            sev = getattr(f, "severity", "?")
            parts.append(f"   - [{sev}] {str(desc)[:100]}")
    return "\n".join(parts)


def _build_prompt(diff: str, scan_context: str) -> str:
    cats = ", ".join(_OWASP_CATEGORIES)
    return "\n".join([
        "You are a security reviewer agent. Analyze the code diff for vulnerabilities.",
        "Focus on OWASP Top 10 categories and common security anti-patterns.",
        "",
        "## Security Dimensions",
        f"Check all: {cats}, plus CSRF, SSRF, path traversal,",
        "insecure cryptography, hardcoded credentials, error info leaks.",
        "",
        "## Prior Scan Results (already flagged — do NOT re-report these)",
        "", scan_context, "",
        "## Code Diff", "", f"```diff\n{diff}\n```", "",
        "## Adversarial Verification",
        "For each finding, construct a concrete attack scenario.",
        "Downgrade findings without a plausible attack path to INFO.",
        "",
        "## Output Format",
        "Output a single JSON object:",
        '{"verdict": "PASS|FAIL|CONDITIONAL",',
        ' "summary": "one-line summary",',
        ' "findings": [{"category": "...", "severity": "critical|high|medium|low|info",',
        '   "description": "...", "file": "...", "line": 0, "cwe": "CWE-xxx",',
        '   "attack_vector": "...", "recommendation": "...", "confidence": "high|medium|low"}],',
        ' "recommendations": ["global recommendation", ...]}',
    ])


def _invoke_agent(
    prompt: str, workspace_path: str, *,
    invoke_fn: Callable[[str], str] | None = None,
) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415
    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, cwd=workspace_path,
    ).stdout


def _parse_findings(raw_findings: list[object]) -> list[SecurityFinding]:
    out: list[SecurityFinding] = []
    for f in raw_findings:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "medium")).lower()
        if sev not in _SEV_RANK:
            sev = "medium"
        out.append(SecurityFinding(
            category=str(f.get("category", "unknown")),
            severity=sev,
            description=str(f.get("description", "")),
            file=str(f.get("file", "")),
            line=int(f.get("line", 0) or 0),
            cwe=str(f.get("cwe", "")),
            attack_vector=str(f.get("attack_vector", "")),
            recommendation=str(f.get("recommendation", "")),
            confidence=str(f.get("confidence", "medium")).lower(),
        ))
    return out


def _parse_result(raw: str) -> tuple[str, list[SecurityFinding], list[str], str]:
    """Parse AI output -> (verdict, findings, recommendations, summary)."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\"verdict\".*\}", text, re.DOTALL)
    if not match:
        return "PASS", [], [], "No parseable review output."
    data: dict[str, object] = json.loads(match.group(0))
    raw_verdict = str(data.get("verdict", "PASS")).upper()
    if "FAIL" in raw_verdict:
        verdict = "FAIL"
    elif "COND" in raw_verdict:
        verdict = "CONDITIONAL"
    else:
        verdict = "PASS"
    findings = _parse_findings(data.get("findings", []))  # type: ignore[arg-type]
    raw_recs = data.get("recommendations", [])
    recs = [str(r) for r in raw_recs] if isinstance(raw_recs, list) else []
    summary = str(data.get("summary", ""))
    # Derive verdict from findings if AI returned PASS but has critical/high findings
    if verdict == "PASS" and any(_SEV_RANK.get(f.severity, 0) >= 3 for f in findings):
        verdict = "FAIL"
    elif verdict == "PASS" and any(_SEV_RANK.get(f.severity, 0) >= 2 for f in findings):
        verdict = "CONDITIONAL"
    return verdict, findings, recs, summary


def run_security_review(
    workspace: Workspace, diff: str,
    scan_results: list[Any] | None = None, *,
    invoke_fn: Callable[[str], str] | None = None,
) -> SecurityReviewResult:
    """Claude-powered diff-focused security review with prior scan context."""
    if not diff.strip():
        logger.info("Security review: empty diff — auto PASS")
        return SecurityReviewResult(
            verdict="PASS", findings=(), recommendations=(), summary="No diff to review.",
        )
    scan_ctx = _format_scan_context(scan_results or [])
    prompt = _build_prompt(diff, scan_ctx)
    logger.info("Invoking AI security review (%d-char diff)", len(diff))
    try:
        raw = _invoke_agent(prompt, workspace.path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("AI security review failed: %s", exc)
        return SecurityReviewResult(
            verdict="CONDITIONAL", findings=(), recommendations=(),
            summary=f"AI review unavailable: {exc}", raw_output="",
        )
    try:
        verdict, findings, recs, summary = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Failed to parse security review output: %s", exc)
        return SecurityReviewResult(
            verdict="CONDITIONAL", findings=(), recommendations=(),
            summary=f"Parse error: {exc}", raw_output=raw,
        )
    logger.info("Security review: %s (%d finding(s))", verdict, len(findings))
    return SecurityReviewResult(
        verdict=verdict, findings=tuple(findings), recommendations=tuple(recs),
        summary=summary, raw_output=raw,
    )


GATE_NAME = "ai-security-review"


def create_runner(workspace: str | Path, *, metrics_dir: str | Path | None = None) -> GateRunner:
    """Create a configured AI security review gate runner."""
    def _check(ws: str) -> tuple[bool, str]:
        from factory.workspace.manager import Workspace as Ws  # noqa: PLC0415
        result = run_security_review(Ws(name="scan", path=ws, repo_url="", branch=""), "")
        if not result.passed:
            return False, f"AI security review: {result.verdict} ({len(result.findings)} finding(s))"
        return True, f"AI security review OK ({result.summary})"
    return create_scan_gate(GATE_NAME, "ai-security-review", _check, workspace, metrics_dir=metrics_dir)
