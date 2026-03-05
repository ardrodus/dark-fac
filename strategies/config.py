"""App-type configuration -- per-type defaults.

App type is a StrEnum (``"console"`` or ``"web"``).  Use
:func:`get_config` to look up per-type defaults::

    from dark_factory.strategies.config import AppType, get_config
    cfg = get_config(AppType.CONSOLE)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AppType(StrEnum):
    """Application type — determines pipeline behaviour and toolchain."""

    CONSOLE = "console"
    WEB = "web"


@dataclass(frozen=True, slots=True)
class AppTypeConfig:
    """Per-app-type defaults (console CLI vs web app)."""

    name: str
    bootstrap_deps: tuple[str, ...]


DEFAULTS: dict[str, AppTypeConfig] = {
    AppType.CONSOLE: AppTypeConfig(
        name="Console",
        bootstrap_deps=("python", "pip", "twine", "git"),
    ),
    AppType.WEB: AppTypeConfig(
        name="Web",
        bootstrap_deps=("node", "npm", "docker", "git"),
    ),
}


def get_config(app_type: str) -> AppTypeConfig:
    """Look up app-type defaults by name."""
    cfg = DEFAULTS.get(app_type)
    if cfg is None:
        msg = f"Unknown app type: {app_type!r}. Valid: {', '.join(DEFAULTS)}"
        raise ValueError(msg)
    return cfg
