"""AWS deployment strategy.

Cloud-native strategy that favours speed, managed services, and
automated operations.  Maps to the ``aws`` overlay in
``factory/agents/overlays/aws.yaml``.
"""

from __future__ import annotations

from factory.strategies.base import PipelineFlags, StrategyInterface, WriteBoundaries


class AwsStrategy(StrategyInterface):
    """AWS cloud-native deployment strategy.

    Prioritises speed-to-market and operational simplicity by leaning
    on managed AWS services, parallel pipeline stages, and automated
    audit approval.
    """

    def get_name(self) -> str:
        """Return ``'AWS'``."""
        return "AWS"

    def get_overlay_name(self) -> str:
        """Return ``'aws'`` — matches ``factory/agents/overlays/aws.yaml``."""
        return "aws"

    def get_write_boundaries(self) -> WriteBoundaries:
        """Return AWS deployment boundaries.

        Allows multi-region deployment without manual approval and
        supports up to 4 parallel deploys.
        """
        return WriteBoundaries(
            allowed_targets=(
                "EKS clusters (multi-region)",
                "Lambda functions",
                "S3 static assets",
            ),
            requires_approval=False,
            max_parallel_deploys=4,
        )

    def get_agent_count(self) -> int:
        """Return ``4`` — high parallelism for cloud workloads."""
        return 4

    def get_pipeline_flags(self) -> PipelineFlags:
        """Return AWS pipeline flags.

        Enables parallel stages and auto-approval; requires 80%
        coverage minimum with no mandatory manual review.
        """
        return PipelineFlags(
            parallel_stages=True,
            auto_approve_audit=True,
            coverage_target=80,
            require_manual_review=False,
        )
