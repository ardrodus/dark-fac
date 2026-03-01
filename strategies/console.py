"""Console (local development) strategy.

Lightweight single-user strategy for interactive CLI use on a
developer workstation.  Maps to the ``console`` overlay in
``factory/agents/overlays/console.yaml``.
"""

from __future__ import annotations

from factory.strategies.base import PipelineFlags, StrategyInterface, WriteBoundaries


class ConsoleStrategy(StrategyInterface):
    """Console (local development) strategy.

    Optimised for a single developer working interactively via the
    CLI.  Runs stages sequentially with live feedback, auto-approves
    audit when quality gates pass, and targets the local filesystem.
    """

    def get_name(self) -> str:
        """Return ``'Console'``."""
        return "Console"

    def get_overlay_name(self) -> str:
        """Return ``'console'`` — matches ``factory/agents/overlays/console.yaml``."""
        return "console"

    def get_write_boundaries(self) -> WriteBoundaries:
        """Return console deployment boundaries.

        Targets local filesystem only with no approval needed and a
        single deploy at a time.
        """
        return WriteBoundaries(
            allowed_targets=("Local filesystem",),
            requires_approval=False,
            max_parallel_deploys=1,
        )

    def get_agent_count(self) -> int:
        """Return ``1`` — single agent for interactive use."""
        return 1

    def get_pipeline_flags(self) -> PipelineFlags:
        """Return console pipeline flags.

        Runs stages sequentially with auto-approval and lightweight
        review; requires 80% coverage minimum.
        """
        return PipelineFlags(
            parallel_stages=False,
            auto_approve_audit=True,
            coverage_target=80,
            require_manual_review=False,
        )
