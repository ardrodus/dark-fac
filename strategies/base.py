"""Strategy interface -- abstract base class for deployment strategies.

Defines ``StrategyInterface(ABC)`` with abstract methods that each
concrete strategy (aws, on-prem, console) must implement.  Value
objects ``WriteBoundaries`` and ``PipelineFlags`` capture strategy
configuration, and result dataclasses capture operation outcomes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WriteBoundaries:
    """Deployment-target boundaries for a strategy."""

    allowed_targets: tuple[str, ...]
    requires_approval: bool
    max_parallel_deploys: int


@dataclass(frozen=True, slots=True)
class PipelineFlags:
    """Pipeline behaviour knobs for a strategy."""

    parallel_stages: bool
    auto_approve_audit: bool
    coverage_target: int
    require_manual_review: bool


@dataclass(frozen=True, slots=True)
class DeployResult:
    """Result of a deploy operation."""

    success: bool
    endpoint: str
    environment: str
    details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of a validate operation."""

    passed: bool
    checks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseResult:
    """Result of a release operation."""

    success: bool
    version: str
    tag: str
    details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    """Result of a provision operation."""

    success: bool
    resources: tuple[str, ...]
    details: tuple[str, ...]


class StrategyInterface(ABC):
    """Abstract base class for deployment strategies.

    Each concrete strategy provides configuration values that govern
    pipeline behaviour and operational methods for deploy, validate,
    release, and provisioning.
    """

    # -- Configuration getters --

    @abstractmethod
    def get_name(self) -> str:
        """Return a human-readable display name for this strategy."""

    @abstractmethod
    def get_overlay_name(self) -> str:
        """Return the overlay identifier used by the template engine."""

    @abstractmethod
    def get_write_boundaries(self) -> WriteBoundaries:
        """Return the deployment-target boundaries for this strategy."""

    @abstractmethod
    def get_agent_count(self) -> int:
        """Return the maximum number of parallel agents for this strategy."""

    @abstractmethod
    def get_pipeline_flags(self) -> PipelineFlags:
        """Return pipeline behaviour flags for this strategy."""

    @abstractmethod
    def supports_dev_mode(self) -> bool:
        """Return whether this strategy supports ``--dev`` (LocalStack) mode."""

    # -- Operations --

    @abstractmethod
    def deploy(
        self, *, environment: str = "staging", dry_run: bool = False,
    ) -> DeployResult:
        """Deploy the application to the target environment."""

    @abstractmethod
    def validate(self, *, environment: str = "staging") -> ValidationResult:
        """Validate a deployment in the target environment."""

    @abstractmethod
    def release(
        self, *, version: str, environment: str = "production",
    ) -> ReleaseResult:
        """Release a validated deployment to end users."""

    @abstractmethod
    def provision(self, *, dry_run: bool = False) -> ProvisionResult:
        """Provision infrastructure required by this strategy."""

    @abstractmethod
    def bootstrap_deps(self) -> tuple[str, ...]:
        """Return the external tool names this strategy requires."""

    @abstractmethod
    def get_endpoint(self, environment: str = "staging") -> str:
        """Return the service endpoint URL for *environment*."""

    @abstractmethod
    def get_critical_stages(self) -> tuple[str, ...]:
        """Return ordered pipeline stages that must pass before release."""
