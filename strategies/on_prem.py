"""On-premises deployment strategy.

Safety-first strategy that favours sequential execution, manual
approval gates, and strict compliance controls.  Maps to the
``on-prem`` overlay in ``factory/agents/overlays/on-prem.yaml``.
"""

from __future__ import annotations

from factory.strategies.base import PipelineFlags, StrategyInterface, WriteBoundaries


class OnPremStrategy(StrategyInterface):
    """On-premises deployment strategy.

    Prioritises reliability, compliance, and data sovereignty by
    requiring manual approval, sequential pipeline stages, and
    comprehensive test coverage.
    """

    def get_name(self) -> str:
        """Return ``'On-Premises'``."""
        return "On-Premises"

    def get_overlay_name(self) -> str:
        """Return ``'on-prem'`` — matches ``factory/agents/overlays/on-prem.yaml``."""
        return "on-prem"

    def get_write_boundaries(self) -> WriteBoundaries:
        """Return on-premises deployment boundaries.

        Requires manual approval and limits to a single deploy at a
        time for maximum safety.
        """
        return WriteBoundaries(
            allowed_targets=(
                "Kubernetes on bare metal (Rancher)",
                "VMware vSphere VMs",
            ),
            requires_approval=True,
            max_parallel_deploys=1,
        )

    def get_agent_count(self) -> int:
        """Return ``2`` — moderate parallelism for controlled environments."""
        return 2

    def get_pipeline_flags(self) -> PipelineFlags:
        """Return on-premises pipeline flags.

        Runs stages sequentially with mandatory manual review and
        audit; requires 95% coverage minimum.
        """
        return PipelineFlags(
            parallel_stages=False,
            auto_approve_audit=False,
            coverage_target=95,
            require_manual_review=True,
        )
