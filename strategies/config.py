"""Strategy configuration -- per-type deployment defaults.

Strategy is a simple config value (``"console"`` or ``"web"``), not a class
hierarchy.  Use :func:`get_config` to look up defaults::

    from factory.strategies.config import get_config
    cfg = get_config("console")
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Per-strategy deployment defaults."""

    name: str
    agent_count: int
    coverage_target: int
    parallel_stages: bool
    auto_approve_audit: bool
    require_manual_review: bool
    max_parallel_deploys: int
    bootstrap_deps: tuple[str, ...]


DEFAULTS: dict[str, StrategyConfig] = {
    "console": StrategyConfig(
        name="Console",
        agent_count=1,
        coverage_target=80,
        parallel_stages=False,
        auto_approve_audit=True,
        require_manual_review=False,
        max_parallel_deploys=1,
        bootstrap_deps=("python", "pip", "twine", "git"),
    ),
    "web": StrategyConfig(
        name="Web",
        agent_count=3,
        coverage_target=90,
        parallel_stages=True,
        auto_approve_audit=False,
        require_manual_review=True,
        max_parallel_deploys=3,
        bootstrap_deps=("node", "npm", "docker", "git"),
    ),
}


def get_config(strategy: str) -> StrategyConfig:
    """Look up strategy defaults by name."""
    cfg = DEFAULTS.get(strategy)
    if cfg is None:
        msg = f"Unknown strategy: {strategy!r}. Valid: {', '.join(DEFAULTS)}"
        raise ValueError(msg)
    return cfg
