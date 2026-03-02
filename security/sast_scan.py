"""SAST gate — two-layer static analysis: deterministic + AI contextual review."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.security.scan_runner import create_scan_gate, run_tool

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.gates.framework import GateRunner
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
_MODE_THR: dict[str, str | None] = {"strict": "medium", "standard": "high", "audit": None}
_AGENT_TIMEOUT = 300
_SG_SEV = {"error": "high", "warning": "medium", "info": "low"}
_BD_SEV = {"high": "high", "medium": "medium", "low": "low"}
_CRIT_CWES = frozenset({"CWE-89", "CWE-78", "CWE-77", "CWE-94", "CWE-502"})
_EXT_LANG = {".py": "python", ".js": "javascript", ".ts": "typescript", ".java": "java",
             ".go": "go", ".rb": "ruby", ".rs": "rust", ".cs": "csharp", ".php": "php"}
_OT = "p/owasp-top-ten"
_RULESETS: dict[str, list[str]] = {
    "python": ["p/python", _OT], "java": ["p/java", _OT], "go": ["p/golang", _OT],
    "javascript": ["p/javascript", "p/nodejs", _OT], "ruby": ["p/ruby", _OT],
    "typescript": ["p/typescript", "p/nodejs", _OT], "rust": ["p/rust", _OT],
    "csharp": ["p/csharp", _OT], "php": ["p/php", _OT],
}
_SKIP = frozenset({"node_modules", ".git", "__pycache__", ".dark-factory", "venv", ".venv"})


@dataclass(frozen=True, slots=True)
class SastFinding:
    """A single SAST finding from Layer 1 or Layer 2."""
    rule_id: str
    description: str
    severity: str
    file: str
    line: int = 0
    cwe: str = ""
    snippet: str = ""
    source: str = "semgrep"
    confidence: str = "medium"
    false_positive: bool = False


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Aggregated SAST scan result."""
    layer1_findings: tuple[SastFinding, ...]
    layer2_findings: tuple[SastFinding, ...]
    false_positives_removed: int
    mode: str = "standard"
    passed: bool = field(init=False)
    def __post_init__(self) -> None:
        thr = _MODE_THR.get(self.mode)
        if thr is None:
            object.__setattr__(self, "passed", True)
            return
        rank = _SEV_RANK.get(thr, 3)
        eff = self.layer2_findings if self.layer2_findings else self.layer1_findings
        blocked = any(_SEV_RANK.get(f.severity, 0) >= rank and not f.false_positive for f in eff)
        object.__setattr__(self, "passed", not blocked)


def _detect_langs(ws_path: str) -> list[str]:
    seen: set[str] = set()
    ws = Path(ws_path)
    if not ws.is_dir():
        return []
    for p in ws.rglob("*"):
        if not any(s in p.parts for s in _SKIP) and (lang := _EXT_LANG.get(p.suffix)):
            seen.add(lang)
    return sorted(seen)


def _esc(sev: str, cwe: str) -> str:
    return "critical" if cwe in _CRIT_CWES and sev == "high" else sev


def _sg_finding(r: dict[str, Any]) -> SastFinding:
    ex: dict[str, Any] = r.get("extra", {}) or {}
    meta: dict[str, Any] = ex.get("metadata", {}) or {}
    sev = _SG_SEV.get((ex.get("severity") or "info").lower(), "low")
    cwe_r = meta.get("cwe", [])
    cwe = cwe_r[0] if isinstance(cwe_r, list) and cwe_r else (str(cwe_r) if cwe_r else "")
    return SastFinding(
        rule_id=r.get("check_id", "unknown"), description=ex.get("message", ""),
        severity=_esc(sev, cwe), file=r.get("path", "unknown"),
        line=r.get("start", {}).get("line", 0), cwe=cwe,
        snippet=(ex.get("lines") or "")[:120], source="semgrep",
        confidence=(meta.get("confidence") or "medium").lower(),
    )


def _bd_finding(r: dict[str, Any]) -> SastFinding:
    sev = _BD_SEV.get((r.get("issue_severity") or "low").lower(), "low")
    conf = (r.get("issue_confidence") or "medium").lower()
    cwe_r: dict[str, Any] = r.get("issue_cwe", {})
    cwe = f"CWE-{cwe_r['id']}" if isinstance(cwe_r, dict) and cwe_r.get("id") else ""
    return SastFinding(
        rule_id=r.get("test_id", "unknown"), description=r.get("issue_text", ""),
        severity=_esc(sev, cwe), file=r.get("filename", "unknown"), line=r.get("line_number", 0),
        cwe=cwe, snippet=(r.get("code") or "")[:120], source="bandit", confidence=conf,
    )


def _run_semgrep(ws_path: str, langs: list[str]) -> list[SastFinding]:
    cfgs: set[str] = set()
    for lang in langs:
        cfgs.update(_RULESETS.get(lang, []))
    if not cfgs:
        cfgs = {"p/owasp-top-ten", "p/security-audit"}
    cmd = ["semgrep", "scan", "--json", "--quiet"]
    for c in sorted(cfgs):
        cmd.extend(["--config", c])
    cmd.append(ws_path)
    return run_tool("semgrep", cmd,
                    lambda raw: [_sg_finding(r) for r in json.loads(raw).get("results", [])],
                    timeout=120)


def _run_bandit(ws_path: str) -> list[SastFinding]:
    return run_tool("bandit", ["bandit", "-r", ws_path, "-f", "json", "--quiet"],
                    lambda raw: [_bd_finding(r) for r in json.loads(raw).get("results", [])],
                    timeout=120)


def _build_l2_prompt(findings: list[SastFinding], diff: str) -> str:
    fj = json.dumps([
        {"rule_id": f.rule_id, "description": f.description, "severity": f.severity,
         "file": f.file, "line": f.line, "cwe": f.cwe, "snippet": f.snippet,
         "source": f.source, "confidence": f.confidence}
        for f in findings
    ], indent=2)
    return "\n".join([
        "You are a security code reviewer. Analyze SAST findings against the code diff.",
        "Identify false positives by tracing data flow and checking framework protections.",
        "\n## Layer 1 Findings\n", f"```json\n{fj}\n```",
        "\n## Code Diff\n", f"```diff\n{diff}\n```",
        "\n## Output Format\n",
        '{"findings": [{"rule_id": "...", "file": "...", "line": 0,',
        '  "severity": "critical|high|medium|low", "false_positive": true|false,',
        '  "reasoning": "..."}], "summary": "..."}',
    ])


def _invoke_l2(prompt: str, ws_path: str, *, invoke_fn: Callable[[str], str] | None = None) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415
    return run_command(["claude", "-p", prompt, "--output-format", "json"],
                       timeout=_AGENT_TIMEOUT, cwd=ws_path).stdout


def _parse_l2(raw: str, layer1: list[SastFinding]) -> tuple[list[SastFinding], int]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\"findings\".*\}", text, re.DOTALL)
    if not match:
        return layer1, 0
    data = json.loads(match.group(0))
    ai_map: dict[tuple[str, str, int], dict[str, object]] = {}
    for f in data.get("findings", []):
        if isinstance(f, dict):
            ai_map[(str(f.get("rule_id", "")), str(f.get("file", "")), int(f.get("line", 0)))] = f
    result: list[SastFinding] = []
    fp = 0
    for f in layer1:
        ai = ai_map.get((f.rule_id, f.file, f.line), {})
        is_fp = bool(ai.get("false_positive", False))
        if is_fp:
            fp += 1
        new_sev = str(ai.get("severity", f.severity)).lower()
        if new_sev not in _SEV_RANK:
            new_sev = f.severity
        result.append(SastFinding(
            rule_id=f.rule_id, description=f.description, severity=new_sev,
            file=f.file, line=f.line, cwe=f.cwe, snippet=f.snippet,
            source="ai", confidence=f.confidence, false_positive=is_fp,
        ))
    return result, fp


def run_sast_scan(
    workspace: Workspace, diff: str, *, mode: str = "standard",
    invoke_fn: Callable[[str], str] | None = None,
) -> ScanResult:
    """Two-layer SAST scan: deterministic tools + AI contextual review."""
    ws = workspace.path
    langs = _detect_langs(ws)
    logger.info("SAST scan: languages=%s, mode=%s", langs, mode)
    l1: list[SastFinding] = []
    if "python" in langs:
        l1.extend(_run_bandit(ws))
    l1.extend(_run_semgrep(ws, langs))
    logger.info("Layer 1: %d finding(s)", len(l1))
    l2: list[SastFinding] = []
    fp_removed = 0
    if l1:
        try:
            raw = _invoke_l2(_build_l2_prompt(l1, diff), ws, invoke_fn=invoke_fn)
            l2, fp_removed = _parse_l2(raw, l1)
            logger.info("Layer 2: %d finding(s), %d FPs removed", len(l2), fp_removed)
        except Exception:  # noqa: BLE001
            logger.warning("Layer 2 AI review failed — using Layer 1 only")
    if mode not in _MODE_THR:
        mode = "standard"
    return ScanResult(layer1_findings=tuple(l1), layer2_findings=tuple(l2),
                      false_positives_removed=fp_removed, mode=mode)


GATE_NAME = "sast-scan"


def create_runner(workspace: str | Path, *, metrics_dir: str | Path | None = None) -> GateRunner:
    """Create a configured SAST gate runner."""
    def _check(ws: str) -> tuple[bool, str]:
        from factory.workspace.manager import Workspace as Ws  # noqa: PLC0415
        result = run_sast_scan(Ws(name="scan", path=ws, repo_url="", branch=""), "")
        if not result.passed:
            return False, f"SAST blocked: {len(result.layer1_findings)} L1, {len(result.layer2_findings)} L2"
        return True, f"SAST OK ({len(result.layer1_findings)} L1, {result.false_positives_removed} FPs removed)"
    return create_scan_gate(GATE_NAME, "sast-analysis", _check, workspace, metrics_dir=metrics_dir)
