"""Obelisk investigator — bridges alerts to the pipeline engine.

Fires the ``obelisk.dot`` investigation pipeline when the watcher detects
a failure.  Thin wiring only: build context, call ``engine.run_pipeline()``,
extract verdict from result.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path

from dark_factory.obelisk.cache import DedupCache
from dark_factory.obelisk.models import Alert, Investigation
from dark_factory.pipeline.engine import FactoryPipelineEngine


async def investigate(
    alert: Alert,
    factory_workspace: str,
    user_workspace: str,
    *,
    repo: str | None = None,
    dedup_cache: DedupCache | None = None,
) -> Investigation:
    """Run the obelisk investigation pipeline for an alert.

    Parameters
    ----------
    alert:
        The alert that triggered this investigation.
    factory_workspace:
        Path to the factory repo workspace (writable).
    user_workspace:
        Path to the user repo workspace (read-only context).

    Returns
    -------
    Investigation:
        Result with verdict (``FIXED``, ``ESCALATED``, or ``SKIPPED``),
        outcome URL, and duration.
    """
    cache = dedup_cache or DedupCache(factory_workspace, repo=repo)
    hit = cache.check(alert.signature)
    if hit is not None:
        return Investigation(
            id="",
            alert=alert,
            verdict=f"SKIPPED ({hit})",
            outcome_url="",
            duration_s=0.0,
        )

    investigation_id = f"inv-{uuid.uuid4().hex[:8]}"

    context = {
        "workspace": factory_workspace,
        "user_workspace": user_workspace,
        "alert": json.dumps(asdict(alert)),
        "investigation_id": investigation_id,
    }

    engine = FactoryPipelineEngine()
    result = await engine.run_pipeline("obelisk", context)

    verdict = "FIXED" if "fixed" in result.completed_nodes else "ESCALATED"

    outcome_url = _read_outcome_url(factory_workspace, investigation_id)

    cache.record(alert.signature)

    return Investigation(
        id=investigation_id,
        alert=alert,
        verdict=verdict,
        outcome_url=outcome_url,
        duration_s=result.duration_seconds,
    )


def _read_outcome_url(workspace: str, investigation_id: str) -> str:
    """Read the outcome URL written by the pipeline, if available."""
    outcome_path = (
        Path(workspace)
        / ".dark-factory"
        / "obelisk"
        / f"outcome-{investigation_id}.json"
    )
    try:
        data = json.loads(outcome_path.read_text(encoding="utf-8"))
        return str(data.get("url", data.get("issue_url", data.get("pr_url", ""))))
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
