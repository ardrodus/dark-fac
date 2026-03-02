"""Obelisk Layer 2: AI-powered diagnosis with full context collection.

Invokes Claude with rich context (container logs, twin configs, event
history, pipeline log, claude-mem knowledge) when Layer 1 cannot classify.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from factory.integrations.shell import CommandResult, docker, run_command
from factory.obelisk.triage import (
    CATEGORY_VERDICTS,
    DiagnosisCategory,
    DiagnosisResult,
    TriageVerdict,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_STATE_DIR = Path(".dark-factory")
_DIAGNOSE_LOG = ".obelisk-diagnose.jsonl"
_CONTAINER_LOG_LINES = 200
_EVENT_COUNT = 20
_AGENT_TIMEOUT = 120
_REQUIRED_FIELDS = frozenset({
    "root_cause", "component", "detail", "category",
    "confidence", "healing_action", "auto_fixable", "verdict", "evidence",
})


# ── should_invoke ─────────────────────────────────────────────────────

def should_invoke(triage_result: TriageVerdict) -> bool:
    """Determine if Layer 2 AI diagnosis is needed for a given triage verdict."""
    return triage_result == TriageVerdict.ESCALATE_HUMAN


# ── Context collection ────────────────────────────────────────────────

def _collect_container_logs(
    max_lines: int = _CONTAINER_LOG_LINES,
    *, docker_fn: Callable[..., CommandResult] | None = None,
) -> str:
    """Collect recent logs from all running Docker containers."""
    fn = docker_fn or docker
    result = fn(["ps", "--format", "{{.Names}}"], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return "[no containers running]"
    parts: list[str] = []
    for name in result.stdout.strip().splitlines():
        name = name.strip()
        if not name:
            continue
        logs = fn(["logs", name, "--tail", str(max_lines)], check=False)
        parts.append(f"=== {name} ===\n{logs.stdout or logs.stderr}")
    return "\n\n".join(parts) if parts else "[no container logs]"


def _collect_twin_configs(state_dir: Path = _STATE_DIR) -> str:
    """Read twin registry configuration."""
    registry = state_dir / "twins" / "registry.json"
    if registry.is_file():
        try:
            return registry.read_text(encoding="utf-8")
        except OSError:
            return "{}"
    return "{}"


def _collect_event_history(
    count: int = _EVENT_COUNT, state_dir: Path = _STATE_DIR,
) -> str:
    """Read recent Obelisk events from state file."""
    state_file = state_dir / ".obelisk-state.json"
    if not state_file.is_file():
        return "[]"
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        events = data.get("recent_events", [])[:count]
        return json.dumps(events, indent=2)
    except (json.JSONDecodeError, OSError):
        return "[]"


def _collect_pipeline_log(stage: str, max_lines: int = 200) -> str:
    """Find and read the most recent pipeline log for a given stage."""
    log_dir = Path("logs/pipeline")
    if not log_dir.is_dir():
        return "[no pipeline log directory]"
    candidates = sorted(log_dir.glob(f"{stage}-*.log"), reverse=True)
    if not candidates:
        candidates = sorted(log_dir.glob(f"{stage}*.log"), reverse=True)
    if not candidates:
        return f"[no pipeline log for stage: {stage}]"
    try:
        lines = candidates[0].read_text(encoding="utf-8").splitlines()[:max_lines]
        return "\n".join(lines)
    except OSError:
        return "[failed to read pipeline log]"


def _collect_claude_mem() -> str:
    """Placeholder — real claude-mem context is injected by the agent protocol."""
    return "[claude-mem context injected by agent protocol]"


# ── Prompt assembly ───────────────────────────────────────────────────

def _build_prompt(error: str, context: dict[str, object]) -> str:
    """Assemble the full diagnosis prompt with all collected context."""
    stage = str(context.get("stage", "unknown"))
    exit_code = context.get("exit_code", 1)
    triage_json = str(context.get("triage_json", "{}"))
    container_logs = _collect_container_logs()
    twin_configs = _collect_twin_configs()
    events = _collect_event_history()
    pipeline_log = _collect_pipeline_log(stage)
    mem_context = _collect_claude_mem()
    return (
        "## Failure Context\n\n"
        f"**Stage:** {stage}\n**Exit Code:** {exit_code}\n\n"
        f"### Layer 1 Diagnostic Report\n```json\n{triage_json}\n```\n\n"
        f"### Failing Stage Output\n```\n{error[-2000:]}\n```\n\n"
        f"### Pipeline Stage Log\n```\n{pipeline_log}\n```\n\n"
        f"### Container Logs (last {_CONTAINER_LOG_LINES} lines)\n```\n{container_logs}\n```\n\n"
        f"### Twin Configurations\n```json\n{twin_configs}\n```\n\n"
        f"### Obelisk Recent Event History\n```json\n{events}\n```\n\n"
        f"### App Knowledge (claude-mem)\n```\n{mem_context}\n```\n\n"
        "## Instructions\n\n"
        "Analyze all the context above and produce a single JSON diagnosis object.\n"
        "Follow the output format defined in your system prompt exactly.\n"
        "Pay special attention to the Pipeline Stage Log — it often reveals the root cause.\n"
    )


# ── Agent invocation ──────────────────────────────────────────────────

def _invoke_agent(
    prompt: str, *, agent_file: Path | None = None,
    invoke_fn: Callable[[str], str] | None = None,
) -> str:
    """Invoke the Obelisk AI agent (or test double) with the assembled prompt."""
    if invoke_fn is not None:
        return invoke_fn(prompt)
    agent_path = str(agent_file or Path("factory/agents/obelisk.md"))
    result = run_command(
        ["claude", "--system", agent_path, "--output-format", "json", "-p", prompt],
        timeout=_AGENT_TIMEOUT, check=True,
    )
    return result.stdout


# ── JSON validation & parsing ─────────────────────────────────────────

def _validate_json(raw: str) -> dict[str, object]:
    """Parse raw agent output, strip fences, validate required fields."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    data: dict[str, object] = json.loads(text)
    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        msg = f"Missing required fields: {', '.join(sorted(missing))}"
        raise ValueError(msg)
    return data


def _parse_result(data: dict[str, object]) -> DiagnosisResult:
    """Convert validated JSON dict into a typed DiagnosisResult."""
    raw_cat = str(data.get("category", "unknown"))
    try:
        category = DiagnosisCategory(raw_cat)
    except ValueError:
        category = DiagnosisCategory.UNKNOWN
    raw_conf = data.get("confidence", 0.5)
    confidence = max(0.0, min(1.0, float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.5))
    verdict = CATEGORY_VERDICTS.get(category, TriageVerdict.ESCALATE_HUMAN)
    raw_ev = data.get("evidence", [])
    evidence = tuple(str(e) for e in raw_ev) if isinstance(raw_ev, list) else (str(raw_ev),)
    return DiagnosisResult(
        root_cause=str(data.get("root_cause", "")),
        component=str(data.get("component", "")),
        detail=str(data.get("detail", "")),
        category=category, confidence=confidence,
        healing_action=str(data.get("healing_action", "")),
        auto_fixable=bool(data.get("auto_fixable", False)),
        verdict=verdict, evidence=evidence,
    )


def _fallback_diagnosis(error: str) -> DiagnosisResult:
    """Produce a safe fallback when the AI agent fails or returns invalid output."""
    return DiagnosisResult(
        root_cause="AI diagnostic agent failed to produce valid output",
        component="obelisk-diagnose", detail=error[:500],
        category=DiagnosisCategory.UNKNOWN, confidence=0.0,
        healing_action="manual review required", auto_fixable=False,
        verdict=TriageVerdict.ESCALATE_HUMAN, evidence=("diagnosis-agent-failed",),
    )


# ── Logging ───────────────────────────────────────────────────────────

def _log_diagnosis(
    error: str, result: DiagnosisResult, *, state_dir: Path | None = None,
) -> None:
    directory = state_dir or _STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.time(), "error_snippet": error[:200],
        "root_cause": result.root_cause, "category": result.category.value,
        "confidence": result.confidence, "verdict": result.verdict.value,
    }
    with (directory / _DIAGNOSE_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ── Public entry point ────────────────────────────────────────────────

def obelisk_diagnose(
    error: str, context: dict[str, object], *,
    state_dir: Path | None = None,
    agent_file: Path | None = None,
    invoke_fn: Callable[[str], str] | None = None,
) -> DiagnosisResult:
    """Layer 2 AI-powered diagnosis: invoke Claude with full failure context."""
    prompt = _build_prompt(error, context)
    try:
        raw = _invoke_agent(prompt, agent_file=agent_file, invoke_fn=invoke_fn)
        data = _validate_json(raw)
        result = _parse_result(data)
    except (json.JSONDecodeError, ValueError, KeyError, Exception):  # noqa: BLE001
        logger.warning("Layer 2 diagnosis failed", exc_info=True)
        result = _fallback_diagnosis(error)
    _log_diagnosis(error, result, state_dir=state_dir)
    return result
