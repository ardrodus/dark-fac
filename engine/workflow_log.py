"""Shared workflow log for pipeline execution visibility.

Creates a flat text file that both the pipeline runner and the Claude
agents write to. Gives human operators real-time visibility into what
agents are actually doing during execution.

Parity with bash: ``/tmp/df-workflow-{TASK}.log`` created in
``run-pipeline.sh`` and injected via ``agent-protocol.sh``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkflowLog:
    """Shared flat-text log for pipeline execution visibility."""

    def __init__(
        self,
        path: Path,
        issue_number: int = 0,
        repo: str = "",
        strategy: str = "",
    ) -> None:
        self._path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                f.write("=================================================================\n")
                f.write(f"Dark Factory Workflow Log -- Issue #{issue_number}\n")
                f.write(f"Repo:      {repo}\n")
                f.write(f"Started:   {datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
                f.write(f"Strategy:  {strategy}\n")
                f.write("=================================================================\n")
        except OSError:
            logger.warning("Could not create workflow log at %s", path, exc_info=True)

    def log(self, stage: str, action: str, detail: str = "") -> None:
        """Append a timestamped entry.

        Format: ``[2026-03-02T18:30:00Z] stage | ACTION | detail``
        """
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"[{ts}] {stage} | {action}"
        if detail:
            line += f" | {detail}"
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # non-critical

    @property
    def path(self) -> Path:
        """Return the log file path."""
        return self._path
