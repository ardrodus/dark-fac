"""App-type configuration -- config-driven defaults.

App type is a StrEnum (``"console"`` or ``"web"``).  Use
:func:`resolve_app_type` to look up defaults::

    from dark_factory.strategies import AppType, resolve_app_type
    cfg = resolve_app_type(AppType.CONSOLE)
"""

from dark_factory.strategies.config import DEFAULTS, AppType, AppTypeConfig, get_config

resolve_app_type = get_config

__all__ = [
    "AppType",
    "AppTypeConfig",
    "DEFAULTS",
    "resolve_app_type",
]
