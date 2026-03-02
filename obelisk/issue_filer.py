"""Obelisk Layer 3: GitHub issue filer and human escalation.

Files GitHub issues when both Layer 1 and Layer 2 fail. Includes PII/path
sanitization, audit logging, dry-run mode, and rate limiting.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.obelisk.triage import DiagnosisResult

logger = logging.getLogger(__name__)
_STATE_DIR = Path(".dark-factory")
_AUDIT_LOG = ".obelisk-issue-filer.jsonl"
_RATE_FILE = ".issue-filer-rate.json"
_DEFAULT_MAX_PER_HOUR = 3

# Sanitisation patterns — compiled once at import time
_ABS_PATH_RE = re.compile(
    r"(?:[A-Z]:\\[\w.\\/-]+|/(?:home|Users|root)/[\w./-]+|~/[\w./-]+)")
_SECRET_RE = re.compile(
    r"(?:ghp_[a-zA-Z0-9]{36,}|gho_[a-zA-Z0-9]{36,}|sk-[a-zA-Z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}|eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"
    r"|xox[bprs]-[a-zA-Z0-9-]+)")
_ENV_VAR_RE = re.compile(r"(?:export\s+)?[A-Z][A-Z0-9_]{2,}=[^\s\"]+")
_TOKEN_KV_RE = re.compile(
    r"([\"']?\w*(?:secret|password|token|api_?key|access_?key)\w*[\"']?"
    r"\s*[:=]\s*)[^\s,\"'}]+", re.IGNORECASE)
_FACTORY_PREFIX = re.compile(r"(?:factory/|\.dark-factory/)")


@dataclass(frozen=True, slots=True)
class IssueResult:
    """Outcome of an issue-filing attempt."""
    action: str  # "filed", "dry_run", "rate_limited", "error"
    issue_url: str = ""
    issue_number: int | None = None
    title: str = ""
    body: str = ""


@dataclass(frozen=True, slots=True)
class IssueConfig:
    """Tuning knobs for the issue filer."""
    max_per_hour: int = _DEFAULT_MAX_PER_HOUR
    dry_run: bool = False
    state_dir: Path = _STATE_DIR
    repo: str = "pdistefano/dark-factory"
    gh_fn: Any = None  # optional Callable override for testing


# ── Sanitisation ─────────────────────────────────────────────────────

def sanitize_content(text: str) -> str:
    """Remove PII, absolute paths, tokens, and secrets from *text*."""
    def _repl(m: re.Match[str]) -> str:
        return m.group(0) if _FACTORY_PREFIX.search(m.group(0)) else "{redacted-path}"
    out = _ABS_PATH_RE.sub(_repl, text)
    out = _SECRET_RE.sub("{secret-redacted}", out)
    out = _ENV_VAR_RE.sub("{env-redacted}", out)
    return _TOKEN_KV_RE.sub(r"\1{secret-redacted}", out)


# ── Rate limiting ────────────────────────────────────────────────────

def _load_rate_state(state_dir: Path) -> dict[str, Any]:
    rate_file = state_dir / _RATE_FILE
    if rate_file.is_file():
        try:
            return json.loads(rate_file.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def _save_rate_state(state_dir: Path, state: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / _RATE_FILE).write_text(
        json.dumps(state, separators=(",", ":")), encoding="utf-8")

def _check_rate_limit(state_dir: Path, max_per_hour: int) -> bool:
    """Return True if filing is allowed (under the limit)."""
    now = time.time()
    window = [t for t in _load_rate_state(state_dir).get("timestamps", []) if now - t < 3600]
    return len(window) < max_per_hour

def _record_filing(state_dir: Path) -> None:
    now = time.time()
    window = [t for t in _load_rate_state(state_dir).get("timestamps", []) if now - t < 3600]
    window.append(now)
    _save_rate_state(state_dir, {"timestamps": window})


# ── Audit log ────────────────────────────────────────────────────────

def _write_audit(
    state_dir: Path, action: str, diagnosis: DiagnosisResult,
    context: dict[str, object], *, issue_url: str = "",
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.time(), "action": action, "issue_url": issue_url,
        "root_cause": diagnosis.root_cause, "component": diagnosis.component,
        "category": diagnosis.category.value, "confidence": diagnosis.confidence,
        "verdict": diagnosis.verdict.value,
        "context_stage": str(context.get("stage", "")),
    }
    with (state_dir / _AUDIT_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ── Issue body builder ───────────────────────────────────────────────

def _build_title(diagnosis: DiagnosisResult) -> str:
    short = diagnosis.component.replace("factory/scripts/", "").replace("factory/", "")
    return f"[Obelisk] Factory bug in {short}: {diagnosis.root_cause[:60]}"

def _build_body(diagnosis: DiagnosisResult, context: dict[str, object]) -> str:
    stage = str(context.get("stage", "unknown"))
    detail = sanitize_content(diagnosis.detail)
    action = sanitize_content(diagnosis.healing_action)
    evidence = "\n".join(f"- {sanitize_content(e)}" for e in diagnosis.evidence)
    return (
        "## Factory Bug Report\n\n> Auto-filed by Obelisk issue filer\n\n"
        f"**Component:** `{diagnosis.component}`\n"
        f"**Category:** {diagnosis.category.value}\n"
        f"**Confidence:** {diagnosis.confidence:.0%}\n"
        f"**Stage:** {stage}\n\n"
        f"### Root Cause\n{detail}\n\n"
        f"### Suggested Fix\n{action}\n\n"
        f"### Evidence\n{evidence}\n")


# ── Public entry point ───────────────────────────────────────────────

def file_issue(
    diagnosis: DiagnosisResult, context: dict[str, object],
    config: IssueConfig | None = None,
) -> IssueResult:
    """File a GitHub issue (or generate a dry-run preview)."""
    cfg = config or IssueConfig()
    title = _build_title(diagnosis)
    body = _build_body(diagnosis, context)
    # Rate limit check
    if not _check_rate_limit(cfg.state_dir, cfg.max_per_hour):
        logger.warning("Rate limited — skipping issue filing")
        _write_audit(cfg.state_dir, "rate_limited", diagnosis, context)
        return IssueResult(action="rate_limited", title=title, body=body)
    # Dry-run mode
    if cfg.dry_run:
        logger.info("Dry run — issue not posted")
        _write_audit(cfg.state_dir, "dry_run", diagnosis, context)
        return IssueResult(action="dry_run", title=title, body=body)
    # File via gh CLI (or injected test double)
    from factory.integrations.shell import run_command  # noqa: PLC0415
    gh_fn = cfg.gh_fn or (lambda args, **kw: run_command(["gh", *args], **kw))
    try:
        result = gh_fn(
            ["issue", "create", "--repo", cfg.repo, "--title", title,
             "--body", body, "--label", "obelisk-detected,factory-bug"],
            check=True)
        url = result.stdout.strip()
        number = int(url.rstrip("/").rsplit("/", 1)[-1]) if "/" in url else None
    except Exception:  # noqa: BLE001
        logger.warning("Failed to file GitHub issue", exc_info=True)
        _write_audit(cfg.state_dir, "error", diagnosis, context)
        return IssueResult(action="error", title=title, body=body)
    _record_filing(cfg.state_dir)
    _write_audit(cfg.state_dir, "filed", diagnosis, context, issue_url=url)
    logger.info("Filed issue: %s", url)
    return IssueResult(action="filed", issue_url=url, issue_number=number, title=title, body=body)
