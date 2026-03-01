"""Obelisk triage — consolidated diagnosis, triage, and suggestion engine.

Merges obelisk-diagnose.sh and obelisk-suggestions.sh into a single module.
Receives failure context from the recovery dispatcher at Level 3,
runs deterministic pattern matching (Layer 1) and AI diagnosis (Layer 2),
and produces actionable recommendations including improvement suggestions.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from factory.integrations.shell import CommandResult, docker, run_command

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_STATE_DIR = Path(".dark-factory")
_TRIAGE_LOG = ".obelisk-triage.jsonl"
_DIAGNOSE_LOG = ".obelisk-diagnose.jsonl"
_SUGGEST_LOG = ".obelisk-suggestions.jsonl"
_SLOW_PIPELINE_THRESHOLD_S = 1800.0
_CONTAINER_THRESHOLD = 8
_WIREMOCK_THRESHOLD = 4
_AGENT_TIMEOUT = 120


class TriageVerdict(Enum):
    """Layer 1 triage verdicts — deterministic classification."""

    HEAL_AND_RETRY = "HEAL_AND_RETRY"
    RETRY = "RETRY"
    SELF_HEAL_CODE = "SELF_HEAL_CODE"
    SELF_HEAL_INFRA = "SELF_HEAL_INFRA"
    FACTORY_BUG = "FACTORY_BUG"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    SKIP = "SKIP"


class DiagnosisCategory(Enum):
    """Layer 2 AI diagnosis categories."""

    CODE = "code"
    TWIN_CONFIG = "twin-config"
    TWIN_STALE = "twin-stale"
    SEED_DATA = "seed-data"
    CONTAINER = "container"
    NETWORK = "network"
    API_TRANSIENT = "api-transient"
    ENVIRONMENT = "environment"
    TEST_FLAKY = "test-flaky"
    PROMPT_ISSUE = "prompt-issue"
    UNKNOWN = "unknown"


CATEGORY_VERDICTS: dict[DiagnosisCategory, TriageVerdict] = {
    DiagnosisCategory.CODE: TriageVerdict.SELF_HEAL_CODE,
    DiagnosisCategory.TWIN_CONFIG: TriageVerdict.SELF_HEAL_INFRA,
    DiagnosisCategory.TWIN_STALE: TriageVerdict.SELF_HEAL_INFRA,
    DiagnosisCategory.SEED_DATA: TriageVerdict.SELF_HEAL_INFRA,
    DiagnosisCategory.CONTAINER: TriageVerdict.HEAL_AND_RETRY,
    DiagnosisCategory.NETWORK: TriageVerdict.HEAL_AND_RETRY,
    DiagnosisCategory.API_TRANSIENT: TriageVerdict.RETRY,
    DiagnosisCategory.ENVIRONMENT: TriageVerdict.SELF_HEAL_INFRA,
    DiagnosisCategory.TEST_FLAKY: TriageVerdict.HEAL_AND_RETRY,
    DiagnosisCategory.PROMPT_ISSUE: TriageVerdict.RETRY,
    DiagnosisCategory.UNKNOWN: TriageVerdict.ESCALATE_HUMAN,
}


@dataclass(frozen=True, slots=True)
class TriageResult:
    """Result of Layer 1 deterministic triage."""

    stage: str
    exit_code: int
    verdict: TriageVerdict
    category: str
    reason: str
    action: str
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class DiagnosisResult:
    """Result of Layer 2 AI diagnosis."""

    root_cause: str
    component: str
    detail: str
    category: DiagnosisCategory
    confidence: float
    healing_action: str
    auto_fixable: bool
    verdict: TriageVerdict
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Suggestion:
    """Improvement suggestion from post-pipeline analysis."""

    detector: str
    title: str
    component: str
    detail: str
    impact: str
    metric_value: float
    metric_unit: str
    metric_label: str
    priority: str = "low"
    timestamp: float = field(default_factory=time.time)


# ── Pattern detectors (Layer 1) ──────────────────────────────────────

_CLAUDE_API_RE = re.compile(r"overloaded_error|api_error|internal_server_error", re.IGNORECASE)
_RATE_LIMIT_RE = re.compile(r"rate_limit|429\s+Too Many Requests", re.IGNORECASE)
_CONTAINER_RE = re.compile(
    r"container.*(?:exited|not running|unhealthy)|docker.*(?:error|failed)", re.IGNORECASE,
)
_AUTH_RE = re.compile(r"401\s+Unauthorized|403\s+Forbidden|authentication.*failed", re.IGNORECASE)
_NETWORK_RE = re.compile(r"ECONNREFUSED|ETIMEDOUT|DNS.*failed|getaddrinfo.*ENOTFOUND", re.IGNORECASE)
_OOM_RE = re.compile(r"out of memory|OOMKilled|Cannot allocate memory", re.IGNORECASE)
_FACTORY_PATH_RE = re.compile(r"factory/(?:scripts|agents|templates)/")
_FACTORY_TRACE_RE = re.compile(r"(?:File|at)\s+[\"']?factory/")


def _detect_pattern(
    exit_code: int, output: str,
) -> tuple[TriageVerdict, str, str, str]:
    """Run deterministic pattern matching: (verdict, category, reason, action)."""
    if exit_code == 0:
        return TriageVerdict.SKIP, "success", "Stage passed", "none"
    if _FACTORY_PATH_RE.search(output) or _FACTORY_TRACE_RE.search(output):
        return TriageVerdict.FACTORY_BUG, "factory", "Error in factory code", "route-to-factory-pipeline"
    if _CLAUDE_API_RE.search(output):
        return TriageVerdict.RETRY, "api-transient", "Claude API error", "retry"
    if _RATE_LIMIT_RE.search(output):
        return TriageVerdict.RETRY, "api-transient", "Rate limited", "wait-and-retry"
    if _OOM_RE.search(output):
        return TriageVerdict.HEAL_AND_RETRY, "container", "Out of memory", "restart-container"
    if _CONTAINER_RE.search(output):
        return TriageVerdict.HEAL_AND_RETRY, "container", "Container failure", "restart-container"
    if _NETWORK_RE.search(output):
        return TriageVerdict.HEAL_AND_RETRY, "network", "Network failure", "retry-with-backoff"
    if _AUTH_RE.search(output):
        return TriageVerdict.SELF_HEAL_INFRA, "environment", "Auth failure", "check-credentials"
    return TriageVerdict.ESCALATE_HUMAN, "unknown", "No pattern matched", "escalate"


# ── Layer 1: Triage ──────────────────────────────────────────────────


def triage(
    stage: str, exit_code: int, output: str, *, state_dir: Path | None = None,
) -> TriageResult:
    """Run Layer 1 deterministic triage on a pipeline stage failure."""
    verdict, category, reason, action = _detect_pattern(exit_code, output)
    result = TriageResult(
        stage=stage, exit_code=exit_code, verdict=verdict,
        category=category, reason=reason, action=action,
    )
    _log_triage(result, state_dir=state_dir)
    return result


def _log_triage(result: TriageResult, *, state_dir: Path | None = None) -> None:
    directory = state_dir or _STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": result.timestamp, "stage": result.stage,
        "exit_code": result.exit_code, "verdict": result.verdict.value,
        "category": result.category, "reason": result.reason, "action": result.action,
    }
    with (directory / _TRIAGE_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ── Layer 2: AI Diagnosis ────────────────────────────────────────────


def diagnose(
    stage: str, exit_code: int, output: str, triage_result: TriageResult, *,
    state_dir: Path | None = None, agent_file: Path | None = None,
    invoke_fn: Callable[[str], str] | None = None,
) -> DiagnosisResult:
    """Run Layer 2 AI diagnosis when Layer 1 returns ESCALATE_HUMAN."""
    prompt = _build_diagnosis_prompt(stage, exit_code, output, triage_result)
    try:
        raw = _invoke_agent(prompt, agent_file=agent_file, invoke_fn=invoke_fn)
        result = _parse_diagnosis(raw)
    except (json.JSONDecodeError, KeyError, ValueError, Exception):  # noqa: BLE001
        logger.warning("Diagnosis agent failed for stage %s", stage, exc_info=True)
        result = _fallback_diagnosis(stage, output)
    _log_diagnosis(stage, exit_code, result, state_dir=state_dir)
    return result


def _build_diagnosis_prompt(
    stage: str, exit_code: int, output: str, triage_result: TriageResult,
) -> str:
    return (
        f"Stage: {stage}\nExit code: {exit_code}\n"
        f"Layer 1 verdict: {triage_result.verdict.value}\n"
        f"Layer 1 reason: {triage_result.reason}\n\n"
        f"Output (last 2000 chars):\n{output[-2000:]}\n\n"
        "Diagnose the root cause and provide a structured JSON response."
    )


def _invoke_agent(
    prompt: str, *, agent_file: Path | None = None,
    invoke_fn: Callable[[str], str] | None = None,
) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    agent_path = str(agent_file or Path("factory/agents/obelisk.md"))
    result = run_command(
        ["claude", "--system", agent_path, "--output-format", "json", "-p", prompt],
        timeout=_AGENT_TIMEOUT, check=True,
    )
    return result.stdout


def _parse_diagnosis(raw: str) -> DiagnosisResult:
    """Parse AI agent JSON output into a DiagnosisResult."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    data: dict[str, object] = json.loads(text)
    raw_cat = str(data.get("category", "unknown"))
    try:
        category = DiagnosisCategory(raw_cat)
    except ValueError:
        category = DiagnosisCategory.UNKNOWN
    raw_conf = data.get("confidence", 0.5)
    confidence = max(0.0, min(1.0, float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.5))
    verdict = CATEGORY_VERDICTS.get(category, TriageVerdict.ESCALATE_HUMAN)
    raw_evidence = data.get("evidence", [])
    evidence = tuple(str(e) for e in raw_evidence) if isinstance(raw_evidence, list) else (str(raw_evidence),)
    return DiagnosisResult(
        root_cause=str(data.get("root_cause", "")),
        component=str(data.get("component", "")),
        detail=str(data.get("detail", "")),
        category=category, confidence=confidence,
        healing_action=str(data.get("healing_action", "")),
        auto_fixable=bool(data.get("auto_fixable", False)),
        verdict=verdict, evidence=evidence,
    )


def _fallback_diagnosis(stage: str, output: str) -> DiagnosisResult:
    return DiagnosisResult(
        root_cause=f"Automated diagnosis failed for stage {stage}",
        component=stage, detail=output[:500],
        category=DiagnosisCategory.UNKNOWN, confidence=0.0,
        healing_action="manual review required", auto_fixable=False,
        verdict=TriageVerdict.ESCALATE_HUMAN, evidence=("diagnosis-agent-failed",),
    )


def _log_diagnosis(
    stage: str, exit_code: int, result: DiagnosisResult, *, state_dir: Path | None = None,
) -> None:
    directory = state_dir or _STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.time(), "stage": stage, "exit_code": exit_code,
        "root_cause": result.root_cause, "category": result.category.value,
        "confidence": result.confidence, "verdict": result.verdict.value,
    }
    with (directory / _DIAGNOSE_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ── Suggestions ──────────────────────────────────────────────────────


def detect_slow_pipeline(
    duration_s: float, *, threshold_s: float = _SLOW_PIPELINE_THRESHOLD_S,
) -> Suggestion | None:
    """Detect pipelines exceeding duration threshold."""
    if duration_s <= threshold_s:
        return None
    minutes = int(duration_s / 60)
    threshold_min = int(threshold_s / 60)
    return Suggestion(
        detector="slow_pipeline",
        title=f"Pipeline took {minutes} min — consider optimization",
        component="factory/pipeline", impact=f"Could save ~{minutes - threshold_min} min per run",
        detail=f"Pipeline duration ({minutes} min) exceeded {threshold_min} min threshold.",
        metric_value=duration_s, metric_unit="seconds", metric_label="pipeline_duration",
    )


def detect_container_consolidation(
    *, docker_fn: Callable[..., CommandResult] | None = None,
) -> Suggestion | None:
    """Detect excessive container counts suggesting consolidation."""
    fn = docker_fn or docker
    result = fn(["ps", "--format", "{{.Names}}"], check=False)
    if result.returncode != 0:
        return None
    containers = [c.strip() for c in result.stdout.strip().split("\n") if c.strip()]
    wiremock = [c for c in containers if "wiremock" in c.lower()]
    if len(wiremock) >= _WIREMOCK_THRESHOLD:
        target = max(1, len(wiremock) // 3)
        return Suggestion(
            detector="container_consolidation",
            title=f"{len(wiremock)} WireMock containers — consolidate to {target}",
            component="factory/scripts",
            detail=f"Found {len(wiremock)} WireMock containers (threshold: {_WIREMOCK_THRESHOLD}).",
            impact=f"Reduce memory by ~{(len(wiremock) - target) * 256}MB",
            metric_value=float(len(wiremock)), metric_unit="containers",
            metric_label="wiremock_count",
        )
    if len(containers) >= _CONTAINER_THRESHOLD:
        return Suggestion(
            detector="container_consolidation",
            title=f"{len(containers)} containers — consider consolidation",
            component="factory/scripts",
            detail=f"Found {len(containers)} total containers (threshold: {_CONTAINER_THRESHOLD}).",
            impact="Reduce startup time and memory usage",
            metric_value=float(len(containers)), metric_unit="containers",
            metric_label="container_count",
        )
    return None


def detect_missing_patterns(workspace: Path) -> Suggestion | None:
    """Detect languages present in workspace but missing factory patterns."""
    indicators = {
        "Cargo.toml": "rust", "go.mod": "go", "pom.xml": "java",
        "build.gradle": "java", "package.json": "node",
    }
    detected = {lang for marker, lang in indicators.items() if (workspace / marker).exists()}
    patterns_dir = workspace / "factory" / "patterns"
    covered = {p.stem for p in patterns_dir.iterdir()} if patterns_dir.is_dir() else set()
    missing = detected - covered
    if not missing:
        return None
    return Suggestion(
        detector="missing_patterns",
        title=f"Missing factory patterns for: {', '.join(sorted(missing))}",
        component="factory/patterns",
        detail=f"Detected languages {sorted(missing)} but no factory patterns found.",
        impact="Add language-specific templates for better code generation",
        metric_value=float(len(missing)), metric_unit="languages",
        metric_label="missing_patterns",
    )


def detect_test_coverage_gaps(workspace: Path) -> Suggestion | None:
    """Detect API/schema files without corresponding tests."""
    gaps: list[str] = []
    if list(workspace.glob("**/*.graphql")) and not list(workspace.glob("**/test*graphql*")):
        gaps.append("GraphQL schemas without GraphQL tests")
    if list(workspace.glob("**/openapi*.yaml")) and not list(workspace.glob("**/test*contract*")):
        gaps.append("OpenAPI specs without contract tests")
    if not gaps:
        return None
    return Suggestion(
        detector="test_coverage_gaps",
        title=f"{len(gaps)} test coverage gap(s) detected",
        component="tests/", detail="; ".join(gaps),
        impact="Improve test coverage for API contracts",
        metric_value=float(len(gaps)), metric_unit="gaps", metric_label="coverage_gaps",
    )


def run_suggestions(
    workspace: Path, duration_s: float = 0.0, *,
    docker_fn: Callable[..., CommandResult] | None = None,
    state_dir: Path | None = None,
) -> list[Suggestion]:
    """Run all suggestion detectors and return findings."""
    suggestions: list[Suggestion] = []
    detectors: list[Callable[[], Suggestion | None]] = [
        lambda: detect_slow_pipeline(duration_s),
        lambda: detect_container_consolidation(docker_fn=docker_fn),
        lambda: detect_missing_patterns(workspace),
        lambda: detect_test_coverage_gaps(workspace),
    ]
    for detector in detectors:
        try:
            result = detector()
            if result is not None:
                suggestions.append(result)
        except Exception:  # noqa: BLE001
            logger.warning("Suggestion detector failed", exc_info=True)
    _log_suggestions(suggestions, state_dir=state_dir)
    return suggestions


def _log_suggestions(
    suggestions: list[Suggestion], *, state_dir: Path | None = None,
) -> None:
    if not suggestions:
        return
    directory = state_dir or _STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / _SUGGEST_LOG).open("a", encoding="utf-8") as fh:
        for sug in suggestions:
            entry = {
                "timestamp": sug.timestamp, "detector": sug.detector,
                "title": sug.title, "priority": sug.priority,
            }
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


# ── Dispatcher Integration ───────────────────────────────────────────


def run_triage(
    context: str, details: dict[str, object], *,
    state_dir: Path | None = None,
    invoke_fn: Callable[[str], str] | None = None,
) -> bool:
    """Entry point for recovery dispatcher (Level 3 ``diagnose_fn``).

    Receives failure context, runs Layer 1 triage and (if needed) Layer 2
    AI diagnosis. Returns ``True`` if the failure was diagnosed with a
    resolution path, ``False`` if escalation to human is required.
    """
    raw_stage = details.get("stage", context)
    stage = str(raw_stage)
    raw_exit = details.get("exit_code", 1)
    exit_code = int(raw_exit) if isinstance(raw_exit, (int, float)) else 1
    raw_output = details.get("output", "")
    output = str(raw_output)

    triage_result = triage(stage, exit_code, output, state_dir=state_dir)

    if triage_result.verdict == TriageVerdict.SKIP:
        return True
    if triage_result.verdict in (TriageVerdict.RETRY, TriageVerdict.HEAL_AND_RETRY):
        return True
    if triage_result.verdict in (TriageVerdict.SELF_HEAL_CODE, TriageVerdict.SELF_HEAL_INFRA):
        return True

    if triage_result.verdict == TriageVerdict.ESCALATE_HUMAN:
        diagnosis = diagnose(
            stage, exit_code, output, triage_result,
            state_dir=state_dir, invoke_fn=invoke_fn,
        )
        return diagnosis.verdict != TriageVerdict.ESCALATE_HUMAN

    return False
