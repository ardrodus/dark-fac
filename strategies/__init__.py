"""Deployment strategies -- abstract interface and concrete implementations.

Re-exports the strategy ABC, value objects, result types, and all
concrete strategy classes for convenient access::

    from factory.strategies import StrategyInterface, AwsStrategy
"""

from factory.strategies.aws import AwsStrategy
from factory.strategies.base import (
    DeployResult,
    PipelineFlags,
    ProvisionResult,
    ReleaseResult,
    StrategyInterface,
    ValidationResult,
    WriteBoundaries,
)
from factory.strategies.console import ConsoleStrategy
from factory.strategies.on_prem import OnPremStrategy

_STRATEGY_MAP: dict[str, type[StrategyInterface]] = {
    "aws": AwsStrategy,
    "on-prem": OnPremStrategy,
    "console": ConsoleStrategy,
}


def resolve_strategy(name: str) -> StrategyInterface:
    """Instantiate a strategy by its config name (``aws``, ``on-prem``, ``console``)."""
    cls = _STRATEGY_MAP.get(name)
    if cls is None:
        msg = f"Unknown strategy: {name!r}. Valid: {', '.join(_STRATEGY_MAP)}"
        raise ValueError(msg)
    return cls()


__all__ = [
    "AwsStrategy",
    "ConsoleStrategy",
    "DeployResult",
    "OnPremStrategy",
    "PipelineFlags",
    "ProvisionResult",
    "ReleaseResult",
    "StrategyInterface",
    "ValidationResult",
    "WriteBoundaries",
    "resolve_strategy",
]
