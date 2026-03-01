"""Centralised configuration loading, validation, and persistence.

Priority (highest → lowest): env vars → ``.env`` → ``config.json`` → defaults.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

_PROJECT_DIR_NAME = ".dark-factory"
_CONFIG_FILE_NAME = "config.json"
_ENV_PREFIX = "DARK_FACTORY_"

# Schema: top-level keys and their expected types.
_SCHEMA: dict[str, type] = {
    "migration": dict,
    "shell": dict,
    "project": dict,
}

_DEFAULTS: dict[str, Any] = {
    "migration": {"manifest_path": "factory/core/migration_manifest.yaml"},
    "shell": {"timeout": 60, "retries": 1},
    "project": {"name": "dark-factory", "version": "6.0.0-dev"},
}


class ConfigError(Exception):
    """Raised when configuration loading or validation fails."""


@dataclass(frozen=True, slots=True)
class ConfigValidationResult:
    """Result of config validation."""

    passed: bool
    issues: tuple[str, ...]


@dataclass(slots=True)
class ConfigData:
    """In-memory configuration store loaded from all sources."""

    data: dict[str, Any] = field(default_factory=dict)
    config_path: Path | None = None
    env_path: Path | None = None


def resolve_config_dir(start: Path | None = None) -> Path:
    """Return the ``.dark-factory`` directory, searching upward from *start*.

    Falls back to ``cwd / .dark-factory`` if not found.
    """
    current = (start or Path.cwd()).resolve()
    for parent in (current, *current.parents):
        candidate = parent / _PROJECT_DIR_NAME
        if candidate.is_dir():
            return candidate
    return current / _PROJECT_DIR_NAME


def resolve_config_path(start: Path | None = None) -> Path:
    """Return the full path to ``config.json``."""
    return resolve_config_dir(start) / _CONFIG_FILE_NAME


def resolve_env_path(start: Path | None = None) -> Path:
    """Return the ``.env`` file adjacent to the project dir."""
    config_dir = resolve_config_dir(start)
    return config_dir.parent / ".env"


def _load_json_config(path: Path) -> dict[str, Any]:
    """Load and parse a JSON config file, returning ``{}`` if missing."""
    if not path.is_file():
        logger.debug("Config file not found: %s", path)
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data: Any = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(f"Failed to load config from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a JSON object, got {type(data).__name__}")
    return dict(data)


def _load_env_values(env_path: Path) -> dict[str, str]:
    """Load key-value pairs from a ``.env`` file via python-dotenv."""
    if not env_path.is_file():
        logger.debug(".env file not found: %s", env_path)
        return {}
    values = dotenv_values(env_path, encoding="utf-8")
    return {k: v for k, v in values.items() if v is not None}


def _collect_env_overrides() -> dict[str, str]:
    """Collect ``DARK_FACTORY_*`` env vars; ``__`` is the nested-key separator."""
    overrides: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith(_ENV_PREFIX):
            suffix = key[len(_ENV_PREFIX):].lower().replace("__", ".")
            overrides[suffix] = value
    return overrides


def _apply_dotted(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted key path."""
    parts = dotted_key.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _get_dotted(data: dict[str, Any], dotted_key: str) -> Any:
    """Retrieve a value from a nested dict; returns ``None`` if missing."""
    parts = dotted_key.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _coerce_value(raw: str) -> int | float | bool | str:
    """Best-effort coercion of string env values to native types."""
    if raw.lower() in ("true", "yes", "1"):
        return True
    if raw.lower() in ("false", "no", "0"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


# ── Public API ─────────────────────────────────────────────────────


def load_config(
    start: Path | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> ConfigData:
    """Load configuration from all sources and merge by priority."""
    config_path = resolve_config_path(start)
    env_path = resolve_env_path(start)

    base = copy.deepcopy(defaults if defaults is not None else _DEFAULTS)
    json_data = _load_json_config(config_path)
    _deep_merge(base, json_data)

    env_values = _load_env_values(env_path)
    for key, raw in env_values.items():
        upper = key.upper()
        if upper.startswith(_ENV_PREFIX.rstrip("_")):
            suffix = upper[len(_ENV_PREFIX):].lower().replace("__", ".")
            _apply_dotted(base, suffix, _coerce_value(raw))
        elif "." in key or "__" in key:
            dotted = key.lower().replace("__", ".")
            _apply_dotted(base, dotted, _coerce_value(raw))

    for dotted_key, raw in _collect_env_overrides().items():
        _apply_dotted(base, dotted_key, _coerce_value(raw))

    logger.debug("Config loaded (json=%s, env=%s)", config_path, env_path)
    return ConfigData(data=base, config_path=config_path, env_path=env_path)


def save_config(cfg: ConfigData) -> None:
    """Persist the current config to its JSON file."""
    if cfg.config_path is None:
        raise ConfigError("Cannot save: no config_path set")
    cfg.config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg.config_path.write_text(
            json.dumps(cfg.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ConfigError(f"Failed to save config to {cfg.config_path}: {exc}") from exc
    logger.info("Config saved to %s", cfg.config_path)


def get_config_value(cfg: ConfigData, key: str) -> Any:
    """Return a config value by dotted key (e.g. ``"shell.timeout"``)."""
    return _get_dotted(cfg.data, key)


def set_config_value(cfg: ConfigData, key: str, value: Any) -> None:
    """Set a config value by dotted key (e.g. ``"shell.timeout"``)."""
    _apply_dotted(cfg.data, key, value)


def validate_config(cfg: ConfigData) -> ConfigValidationResult:
    """Validate config data against the expected schema."""
    issues: list[str] = []
    for section, expected_type in _SCHEMA.items():
        val = cfg.data.get(section)
        if val is None:
            issues.append(f"Missing required section: {section!r}")
        elif not isinstance(val, expected_type):
            issues.append(
                f"Section {section!r}: expected {expected_type.__name__}, "
                f"got {type(val).__name__}"
            )
    shell = cfg.data.get("shell")
    if isinstance(shell, dict):
        timeout = shell.get("timeout")
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
            issues.append(f"shell.timeout must be a positive number, got {timeout!r}")
    return ConfigValidationResult(passed=len(issues) == 0, issues=tuple(issues))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Recursively merge *override* into *base* (mutates *base*)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
