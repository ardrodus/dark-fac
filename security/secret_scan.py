"""Secret scan gate — detect hardcoded credentials, API keys, and secrets."""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from factory.security.scan_runner import create_scan_gate

if TYPE_CHECKING:
    from factory.gates.framework import GateRunner
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)

_SECRET_PATTERNS: list[tuple[str, str, str]] = [
    ("AWS Access Key ID", r"(?:^|[^A-Z0-9])AKIA[0-9A-Z]{16}(?:$|[^A-Z0-9])", "critical"),
    ("AWS Secret Key", r"(?:aws_secret_access_key|secret_key)\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}", "critical"),
    ("GitHub Token (ghp)", r"ghp_[A-Za-z0-9]{36,}", "critical"),
    ("GitHub Token (gho)", r"gho_[A-Za-z0-9]{36,}", "critical"),
    ("GitHub Token (ghu)", r"ghu_[A-Za-z0-9]{36,}", "critical"),
    ("GitHub Token (ghs)", r"ghs_[A-Za-z0-9]{36,}", "critical"),
    ("GitHub PAT", r"github_pat_[A-Za-z0-9_]{22,}", "critical"),
    ("Private Key Header", r"-----BEGIN (?:RSA |DSA |EC )?PRIVATE KEY-----", "critical"),
    ("Generic API Key", r"(?:api[_-]?key|apikey)\s*[=:]\s*['\"][A-Za-z0-9]{20,}['\"]", "warning"),
    ("Generic Secret", r"(?:secret|password|passwd|pwd)\s*[=:]\s*['\"][^\s'\"]{8,}['\"]", "warning"),
    ("JWT Secret", r"(?:jwt[_-]?secret|JWT_SECRET)\s*[=:]\s*['\"][^\s'\"]{8,}['\"]", "critical"),
    ("Slack Token", r"xox[bpoas]-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,}", "critical"),
    ("Slack Webhook", r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+", "warning"),
    ("Stripe Key", r"[sr]k_(?:live|test)_[A-Za-z0-9]{20,}", "critical"),
    ("SendGrid Key", r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}", "critical"),
    ("Twilio Key", r"SK[0-9a-fA-F]{32}", "warning"),
    ("Base64 High-Entropy", r"(?:token|key|secret|password)\s*[=:]\s*['\"][A-Za-z0-9+/]{40,}={0,2}['\"]", "warning"),
]
_COMPILED = [(n, re.compile(p), s) for n, p, s in _SECRET_PATTERNS]
_TEST_MARKERS = frozenset({"test", "example", "sample", "fixture", "mock", "dummy", "fake", "placeholder", "testdata"})
_ENTROPY_THR, _MIN_TOK = 4.5, 20
_ASSIGN_RE = re.compile(
    r"(?:key|secret|token|password|passwd|credential|auth)\s*[=:]\s*['\"]?"
    r"([A-Za-z0-9+/=_\-]{20,})['\"]?",
    re.IGNORECASE,
)


def _entropy(data: str) -> float:
    if not data:
        return 0.0
    freq: dict[str, int] = {}
    for ch in data:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(data)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """A single detected secret."""
    file: str
    line: int
    rule: str
    severity: str
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Aggregated result of a secret scan."""
    findings: tuple[SecretFinding, ...]
    passed: bool = field(init=False)
    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", not any(f.severity == "critical" for f in self.findings))


def _load_allowlist(workspace_path: str) -> set[str]:
    exc_path = Path(workspace_path) / ".dark-factory" / "security-exceptions.json"
    if not exc_path.is_file():
        return set()
    try:
        return {str(e) for e in json.loads(exc_path.read_text(encoding="utf-8")).get("allowed", [])}
    except (json.JSONDecodeError, OSError):
        return set()


def _adjust_sev(severity: str, file_path: str) -> str:
    return "info" if any(m in file_path.lower() for m in _TEST_MARKERS) else severity


def _scan_diff(diff: str, workspace_path: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    current_file, line_num = "unknown", 0
    allowlist = _load_allowlist(workspace_path)
    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file, line_num = raw_line[6:], 0
            continue
        if raw_line.startswith("@@ "):
            m = re.search(r"\+(\d+)", raw_line)
            line_num = int(m.group(1)) - 1 if m else 0
            continue
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        line_num += 1
        content = raw_line[1:]
        for name, pattern, severity in _COMPILED:
            if pattern.search(content):
                f = SecretFinding(current_file, line_num, name,
                                  _adjust_sev(severity, current_file), content.strip()[:80])
                if f.rule not in allowlist and f.snippet not in allowlist:
                    findings.append(f)
        for tok_m in _ASSIGN_RE.finditer(content):
            tok = tok_m.group(1)
            if len(tok) >= _MIN_TOK and _entropy(tok) >= _ENTROPY_THR:
                f = SecretFinding(current_file, line_num, "high-entropy-string",
                                  _adjust_sev("warning", current_file), tok[:80])
                if f.rule not in allowlist and f.snippet not in allowlist:
                    findings.append(f)
    return findings


def run_secret_scan(workspace: Workspace, diff: str) -> ScanResult:
    """Scan *diff* for hardcoded secrets and return a :class:`ScanResult`."""
    findings = _scan_diff(diff, workspace.path)
    logger.info("Secret scan: %d finding(s)", len(findings))
    return ScanResult(findings=tuple(findings))


GATE_NAME = "secret-scan"


def create_runner(workspace: str | Path, *, metrics_dir: str | Path | None = None) -> GateRunner:
    """Create a configured (but not executed) secret-scan gate runner."""
    def _check(ws: str) -> tuple[bool, str]:
        result = _scan_diff("", ws)
        crit = sum(1 for f in result if f.severity == "critical")
        if crit > 0:
            return False, f"Critical secrets found: {crit} finding(s)"
        return True, f"secret scan OK ({len(result)} finding(s), none critical)"
    return create_scan_gate(GATE_NAME, "secret-pattern-scan", _check, workspace, metrics_dir=metrics_dir)
