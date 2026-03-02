"""Architecture review specialist agents — 10 domain experts for issue review.

Ports the specialist agent pipeline from run-pipeline.sh.  Each specialist
reviews an issue through its domain lens (code quality, security, etc.) and
returns structured findings with a risk assessment.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from factory.integrations.shell import run_command

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "agents" / "prompts"
_AGENT_TIMEOUT = 120
_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})


@dataclass(frozen=True, slots=True)
class Specialist:
    """Domain specialist for architecture review."""

    name: str
    role: str
    prompt_template: str
    output_schema: tuple[str, ...] = (
        "findings", "risk_level", "recommendations", "approval",
    )


@dataclass(frozen=True, slots=True)
class SpecialistResult:
    """Structured output from a specialist review."""

    agent_name: str
    findings: tuple[str, ...]
    risk_level: str
    recommendations: tuple[str, ...]
    approval: bool
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


# ── Helpers ──────────────────────────────────────────────────────────


def _load_prompt(template_name: str) -> str:
    """Read a prompt template from factory/agents/prompts/."""
    path = _PROMPTS_DIR / template_name
    return path.read_text(encoding="utf-8")


def _tup(raw: object) -> tuple[str, ...]:
    """Coerce list-or-scalar agent output to a typed tuple."""
    if isinstance(raw, list):
        return tuple(str(item) for item in raw)
    if isinstance(raw, str):
        return (raw,) if raw else ()
    return ()


def _strip_fences(text: str) -> str:
    """Strip markdown code fences from agent output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _format_context(context: dict[str, object]) -> str:
    """Format context dict as readable key-value pairs for the prompt."""
    if not context:
        return "No additional context provided."
    parts: list[str] = []
    for key, val in context.items():
        parts.append(f"- **{key}:** {val}")
    return "\n".join(parts)


def _invoke_agent(
    prompt: str, *, invoke_fn: Callable[[str], str] | None = None,
) -> str:
    """Invoke Claude with the given prompt and return raw output."""
    if invoke_fn is not None:
        return invoke_fn(prompt)
    result = run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, check=True,
    )
    return result.stdout


def _parse_result(agent_name: str, raw: str) -> SpecialistResult:
    """Parse agent JSON output into a SpecialistResult."""
    text = _strip_fences(raw)
    match = re.search(r"\{", text)
    if match:
        text = text[match.start():]
    try:
        data: dict[str, object] = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON from %s", agent_name)
        return SpecialistResult(
            agent_name=agent_name, findings=("Parse error: non-JSON output",),
            risk_level="medium", recommendations=(), approval=False,
            raw_output=raw, errors=("json_parse_error",),
        )
    risk = str(data.get("risk_level", "medium")).lower()
    if risk not in _RISK_LEVELS:
        risk = "medium"
    raw_approval = data.get("approval", False)
    approval = bool(raw_approval) if not isinstance(raw_approval, str) else (
        raw_approval.lower() in ("true", "yes", "approved")
    )
    return SpecialistResult(
        agent_name=agent_name,
        findings=_tup(data.get("findings", [])),
        risk_level=risk,
        recommendations=_tup(data.get("recommendations", [])),
        approval=approval,
        raw_output=raw,
    )


# ── Public API ───────────────────────────────────────────────────────


def run_specialist(
    agent: Specialist,
    issue: dict[str, object],
    context: dict[str, object],
    *,
    invoke_fn: Callable[[str], str] | None = None,
) -> SpecialistResult:
    """Invoke a specialist agent to review an issue.

    Loads the specialist's prompt template, appends the issue details,
    invokes Claude, and parses the structured result.
    """
    try:
        role_def = _load_prompt(agent.prompt_template)
    except FileNotFoundError:
        logger.error("Prompt template not found: %s", agent.prompt_template)
        return SpecialistResult(
            agent_name=agent.name, findings=(), risk_level="medium",
            recommendations=(), approval=False,
            errors=(f"missing_template:{agent.prompt_template}",),
        )
    prompt = (
        f"{role_def}\n\n"
        f"## Issue Under Review\n\n"
        f"**Title:** {issue.get('title', 'N/A')}\n"
        f"**Number:** #{issue.get('number', 'N/A')}\n\n"
        f"{issue.get('body', 'No description provided.')}\n\n"
        f"## Additional Context\n\n{_format_context(context)}\n\n"
        "Respond with a JSON object containing these keys:\n"
        "findings (list of strings), risk_level (low|medium|high|critical), "
        "recommendations (list of strings), approval (boolean)."
    )
    try:
        raw = _invoke_agent(prompt, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Specialist %s failed: %s", agent.name, exc)
        return SpecialistResult(
            agent_name=agent.name, findings=(), risk_level="medium",
            recommendations=(), approval=False,
            errors=(f"invocation_error:{exc}",),
        )
    return _parse_result(agent.name, raw)


# ── Specialist Definitions ───────────────────────────────────────────

SA_CODE_QUALITY = Specialist(
    name="sa-code-quality", role="Code Quality",
    prompt_template="sa-code-quality.md",
)
SA_SECURITY = Specialist(
    name="sa-security", role="Security",
    prompt_template="sa-security.md",
)
SA_INTEGRATION = Specialist(
    name="sa-integration", role="Integration",
    prompt_template="sa-integration.md",
)
SA_PERFORMANCE = Specialist(
    name="sa-performance", role="Performance",
    prompt_template="sa-performance.md",
)
SA_TESTING = Specialist(
    name="sa-testing", role="Testing",
    prompt_template="sa-testing.md",
)
SA_DEPENDENCIES = Specialist(
    name="sa-dependencies", role="Dependencies",
    prompt_template="sa-dependencies.md",
)
SA_API_DESIGN = Specialist(
    name="sa-api-design", role="API Design",
    prompt_template="sa-api-design.md",
)
SA_DATABASE = Specialist(
    name="sa-database", role="Database",
    prompt_template="sa-database.md",
)
SA_UX = Specialist(
    name="sa-ux", role="UX",
    prompt_template="sa-ux.md",
)
SA_DEVOPS = Specialist(
    name="sa-devops", role="DevOps",
    prompt_template="sa-devops.md",
)

ALL_SPECIALISTS: tuple[Specialist, ...] = (
    SA_CODE_QUALITY, SA_SECURITY, SA_INTEGRATION, SA_PERFORMANCE,
    SA_TESTING, SA_DEPENDENCIES, SA_API_DESIGN, SA_DATABASE,
    SA_UX, SA_DEVOPS,
)

SPECIALISTS_BY_NAME: dict[str, Specialist] = {s.name: s for s in ALL_SPECIALISTS}
