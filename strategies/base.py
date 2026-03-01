"""Strategy interface — abstract base class for deployment strategies.

Defines ``StrategyInterface(ABC)`` with abstract methods that each
concrete strategy (aws, on-prem, console) must implement.  Value
objects ``WriteBoundaries`` and ``PipelineFlags`` capture strategy
configuration as frozen dataclasses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WriteBoundaries:
    """Deployment-target boundaries for a strategy.

    Parameters
    ----------
    allowed_targets:
        Tuple of deployment target descriptions the strategy permits.
    requires_approval:
        Whether manual approval is needed before deploying.
    max_parallel_deploys:
        Maximum number of simultaneous deployments allowed.
    """

    allowed_targets: tuple[str, ...]
    requires_approval: bool
    max_parallel_deploys: int


@dataclass(frozen=True, slots=True)
class PipelineFlags:
    """Pipeline behaviour knobs for a strategy.

    Parameters
    ----------
    parallel_stages:
        Whether pipeline stages may execute concurrently.
    auto_approve_audit:
        Whether audit auto-passes when quality gates succeed.
    coverage_target:
        Minimum test-coverage percentage required by this strategy.
    require_manual_review:
        Whether code review by a human is mandatory.
    """

    parallel_stages: bool
    auto_approve_audit: bool
    coverage_target: int
    require_manual_review: bool


class StrategyInterface(ABC):
    """Abstract base class for deployment strategies.

    Each concrete strategy provides configuration values that govern
    pipeline behaviour, agent concurrency, write boundaries, and other
    operational knobs.  Implementations live alongside this base class
    in ``factory/strategies/``.
    """

    @abstractmethod
    def get_name(self) -> str:
        """Return a human-readable display name for this strategy."""

    @abstractmethod
    def get_overlay_name(self) -> str:
        """Return the overlay identifier used by the template engine.

        The value must match a YAML filename (without extension) in
        ``factory/agents/overlays/``.
        """

    @abstractmethod
    def get_write_boundaries(self) -> WriteBoundaries:
        """Return the deployment-target boundaries for this strategy."""

    @abstractmethod
    def get_agent_count(self) -> int:
        """Return the maximum number of parallel agents for this strategy."""

    @abstractmethod
    def get_pipeline_flags(self) -> PipelineFlags:
        """Return pipeline behaviour flags for this strategy."""
