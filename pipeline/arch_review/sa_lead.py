"""SA Lead — aggregates specialist results into a final architecture verdict."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from factory.pipeline.arch_review.specialists import SpecialistResult

logger = logging.getLogger(__name__)


class Verdict(Enum):
    GO = "GO"
    NO_GO = "NO_GO"
    CONDITIONAL = "CONDITIONAL"


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Aggregated risk profile across all specialists."""
    overall_level: str
    critical_count: int
    high_count: int
    risk_areas: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ArchReviewVerdict:
    """Final output of the SA Lead review."""
    verdict: Verdict
    summary: str
    blocking_findings: tuple[str, ...] = field(default_factory=tuple)
    conditions: tuple[str, ...] = field(default_factory=tuple)
    risk_assessment: RiskAssessment = field(
        default_factory=lambda: RiskAssessment("low", 0, 0),
    )


def _assess_risk(results: list[SpecialistResult]) -> RiskAssessment:
    """Aggregate risk from all specialist results."""
    crit = sum(1 for r in results if r.risk_level == "critical")
    high = sum(1 for r in results if r.risk_level == "high")
    areas = tuple(r.agent_name for r in results if r.risk_level in ("critical", "high"))
    if crit:
        overall = "critical"
    elif high:
        overall = "high"
    elif any(r.risk_level == "medium" for r in results):
        overall = "medium"
    else:
        overall = "low"
    return RiskAssessment(overall, crit, high, areas)


def _determine_verdict(results: list[SpecialistResult], risk: RiskAssessment) -> Verdict:
    """NO_GO on critical, CONDITIONAL on high-with-mitigations, else GO."""
    if risk.critical_count > 0:
        return Verdict.NO_GO
    if risk.high_count > 0:
        has_recs = any(r.recommendations for r in results if r.risk_level == "high")
        return Verdict.CONDITIONAL if has_recs else Verdict.NO_GO
    return Verdict.GO


def _collect_blocking(results: list[SpecialistResult]) -> tuple[str, ...]:
    return tuple(
        f"[{r.agent_name}] {f}"
        for r in results if r.risk_level in ("critical", "high")
        for f in r.findings
    )


def _collect_conditions(results: list[SpecialistResult]) -> tuple[str, ...]:
    return tuple(
        f"[{r.agent_name}] {rec}"
        for r in results if r.risk_level == "high"
        for rec in r.recommendations
    )


def _build_summary(results: list[SpecialistResult], v: Verdict, risk: RiskAssessment) -> str:
    total, approved = len(results), sum(1 for r in results if r.approval)
    parts = [f"Architecture review: {v.value}. {approved}/{total} specialists approved."]
    if risk.risk_areas:
        parts.append(f"Risk areas: {', '.join(risk.risk_areas)}.")
    return " ".join(parts)


def _build_l1(results: list[SpecialistResult]) -> str:
    """L1: one-liner markdown table per specialist."""
    hdr = "| Specialist | Risk | Approved | Key Finding |\n|---|---|---|---|"
    rows: list[str] = []
    for r in results:
        finding = (r.findings[0][:60] + "...") if r.findings else "No findings"
        rows.append(f"| {r.agent_name} | {r.risk_level} | {'Yes' if r.approval else 'No'} | {finding} |")
    return hdr + "\n" + "\n".join(rows)


def _build_l2(results: list[SpecialistResult]) -> str:
    """L2: paragraph summary per specialist."""
    parts: list[str] = []
    for r in results:
        findings = "; ".join(r.findings[:3]) if r.findings else "No findings"
        recs = "; ".join(r.recommendations[:2]) if r.recommendations else "None"
        parts.append(f"**{r.agent_name}** ({r.risk_level}): {findings}. Recs: {recs}")
    return "\n\n".join(parts)


def _build_l3(results: list[SpecialistResult]) -> str:
    """L3: full detail dump from every specialist."""
    sections: list[str] = []
    for r in results:
        lines = [f"## {r.agent_name}", f"**Risk:** {r.risk_level}  |  **Approved:** {r.approval}"]
        if r.findings:
            lines.append("**Findings:**\n" + "\n".join(f"- {f}" for f in r.findings))
        if r.recommendations:
            lines.append("**Recs:**\n" + "\n".join(f"- {x}" for x in r.recommendations))
        if r.errors:
            lines.append("**Errors:** " + ", ".join(r.errors))
        sections.append("\n".join(lines))
    return "\n\n---\n\n".join(sections)


def _format_comment(v: ArchReviewVerdict, issue: dict[str, object], l1: str, l2: str) -> str:
    """Format verdict as a GitHub issue comment body."""
    emoji = {"GO": "\u2705", "NO_GO": "\u274c", "CONDITIONAL": "\u26a0\ufe0f"}
    e = emoji.get(v.verdict.value, "")
    num, title = issue.get("number", "?"), issue.get("title", "N/A")
    ra = v.risk_assessment
    lines = [
        f"## {e} Architecture Review \u2014 #{num}: {title}",
        f"\n**Verdict: {v.verdict.value}**\n",
        f"### Summary\n{v.summary}\n",
        "### Risk Assessment",
        f"- **Overall:** {ra.overall_level}",
        f"- **Critical findings:** {ra.critical_count}",
        f"- **High-risk areas:** {ra.high_count}\n",
    ]
    if v.blocking_findings:
        lines.append("### Blocking Findings")
        lines.extend(f"- {f}" for f in v.blocking_findings)
        lines.append("")
    if v.conditions:
        lines.append("### Conditions for Approval")
        lines.extend(f"- {c}" for c in v.conditions)
        lines.append("")
    lines.append(f"### Specialist Overview (L1)\n\n{l1}\n")
    lines.append(f"<details><summary>Specialist Details (L2)</summary>\n\n{l2}\n\n</details>")
    return "\n".join(lines)


def _post_comment(issue: dict[str, object], body: str, *, repo: str | None = None) -> None:
    """Post verdict comment on the GitHub issue."""
    num = issue.get("number")
    if not isinstance(num, (int, float)):
        logger.warning("Cannot post comment: missing issue number")
        return
    try:
        from factory.integrations.shell import gh  # noqa: PLC0415
        args = ["issue", "comment", str(int(num)), "--body", body]
        if repo:
            args.extend(["--repo", repo])
        gh(args, check=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to post verdict comment on #%s: %s", num, exc)


def run_sa_lead(
    specialist_results: list[SpecialistResult],
    issue: dict[str, object],
    *,
    repo: str | None = None,
) -> ArchReviewVerdict:
    """Aggregate specialist results into a final architecture review verdict."""
    risk = _assess_risk(specialist_results)
    vrd = _determine_verdict(specialist_results, risk)
    summary = _build_summary(specialist_results, vrd, risk)
    result = ArchReviewVerdict(
        verdict=vrd, summary=summary,
        blocking_findings=_collect_blocking(specialist_results),
        conditions=_collect_conditions(specialist_results),
        risk_assessment=risk,
    )
    l1, l2 = _build_l1(specialist_results), _build_l2(specialist_results)
    _post_comment(issue, _format_comment(result, issue, l1, l2), repo=repo)
    logger.info("SA Lead verdict: %s for #%s", vrd.value, issue.get("number"))
    return result
