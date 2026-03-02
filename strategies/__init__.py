"""Deployment strategies -- config-driven defaults.

Strategy is a simple config value (``"console"`` or ``"web"``), not a class
hierarchy.  Use :func:`resolve_strategy` to look up defaults::

    from factory.strategies import resolve_strategy
    cfg = resolve_strategy("console")
"""

from factory.strategies.config import DEFAULTS, StrategyConfig, get_config

resolve_strategy = get_config

__all__ = [
    "DEFAULTS",
    "StrategyConfig",
    "resolve_strategy",
]
