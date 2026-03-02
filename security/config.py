"""Security configuration and mode management.

Ported from ``security-config.sh`` (US-023 / SE-10).  Configurable security
policies: strict / standard / audit modes, tool overrides, exception management
with TTL and justification.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_VALID_MODES = frozenset({"strict", "standard", "audit"})
_DEFAULT_TTL_DAYS = 30


@dataclass(slots=True)
class SecurityException:
    """A time-limited security exception for a specific finding."""
    finding_id: str
    file: str
    reason: str
    approved_by: str = ""
    created: str = ""
    expires: str = ""


@dataclass(slots=True)
class SecurityConfig:
    """Security policy configuration loaded from disk."""
    mode: str = "standard"
    tool_overrides: dict[str, str] = field(default_factory=dict)
    exceptions: list[SecurityException] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            logger.warning("Invalid security mode %r, falling back to 'standard'", self.mode)
            self.mode = "standard"

    @property
    def blocking_threshold(self) -> str | None:
        """Minimum severity that blocks: strict->medium, standard->high, audit->None."""
        if self.mode == "strict":
            return "medium"
        if self.mode == "standard":
            return "high"
        return None


def _config_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".dark-factory" / "security-config.json"


def _parse_exc(raw: dict[str, Any]) -> SecurityException:
    return SecurityException(
        finding_id=str(raw.get("finding_id", "")), file=str(raw.get("file", "")),
        reason=str(raw.get("reason", "")), approved_by=str(raw.get("approved_by", "")),
        created=str(raw.get("created", "")), expires=str(raw.get("expires", "")),
    )


def load_security_config(workspace: str | Path) -> SecurityConfig:
    """Load config from ``.dark-factory/security-config.json``; returns defaults on error."""
    cfg_path = _config_path(workspace)
    if not cfg_path.is_file():
        logger.debug("No security config at %s — using defaults", cfg_path)
        return SecurityConfig()
    try:
        data: Any = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load security config from %s: %s", cfg_path, exc)
        return SecurityConfig()
    if not isinstance(data, dict):
        return SecurityConfig()
    mode = str(data.get("mode", "standard")).lower()
    tools = data.get("tool_overrides", {})
    raw_exc = data.get("exceptions", [])
    exceptions = [_parse_exc(e) for e in (raw_exc if isinstance(raw_exc, list) else []) if isinstance(e, dict)]
    return SecurityConfig(
        mode=mode,
        tool_overrides=tools if isinstance(tools, dict) else {},
        exceptions=exceptions,
    )


def save_security_config(workspace: str | Path, config: SecurityConfig) -> None:
    """Persist the security configuration to disk."""
    cfg_path = _config_path(workspace)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "mode": config.mode,
        "tool_overrides": config.tool_overrides,
        "exceptions": [
            {"finding_id": e.finding_id, "file": e.file, "reason": e.reason,
             "approved_by": e.approved_by, "created": e.created, "expires": e.expires}
            for e in config.exceptions
        ],
    }
    cfg_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("Security config saved to %s", cfg_path)


# ── Exception management ────────────────────────────────────────


def add_exception(
    config: SecurityConfig, finding_id: str, file: str, reason: str,
    *, approved_by: str = "", ttl_days: int = _DEFAULT_TTL_DAYS,
) -> SecurityConfig:
    """Add a new exception with TTL. *reason* is required (justification)."""
    if not reason:
        msg = "Exception justification (reason) is required"
        raise ValueError(msg)
    today = date.today()
    exc = SecurityException(
        finding_id=finding_id, file=file, reason=reason, approved_by=approved_by,
        created=today.isoformat(), expires=(today + timedelta(days=ttl_days)).isoformat(),
    )
    config.exceptions.append(exc)
    logger.info("Added exception: %s in %s (expires %s)", finding_id, file, exc.expires)
    return config


def prune_expired(config: SecurityConfig) -> int:
    """Remove expired exceptions. Returns the count of pruned entries."""
    today_str = date.today().isoformat()
    before = len(config.exceptions)
    config.exceptions = [e for e in config.exceptions if e.expires >= today_str]
    pruned = before - len(config.exceptions)
    if pruned:
        logger.info("Pruned %d expired exception(s)", pruned)
    return pruned


def is_excepted(config: SecurityConfig, finding_id: str, file: str) -> bool:
    """Check whether a finding has an active (non-expired) exception."""
    today_str = date.today().isoformat()
    return any(
        e.finding_id == finding_id and e.file == file and e.expires >= today_str
        for e in config.exceptions
    )
