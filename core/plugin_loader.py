"""Event-driven plugin system for pipeline hooks.

Port of ``plugin-loader.sh``.  Discovers Python plugin files in
``factory/plugins/``, loads them, and dispatches named events.
Plugin errors are caught and logged — they never crash the pipeline.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"
_MANIFEST_NAME = "manifest.json"


class Event(Enum):
    """Pipeline events that plugins can subscribe to."""

    PIPELINE_START = "pipeline_start"
    PIPELINE_END = "pipeline_end"
    STAGE_START = "stage_start"
    STAGE_END = "stage_end"
    ISSUE_DISPATCHED = "issue_dispatched"
    PR_CREATED = "pr_created"
    SECURITY_SCAN_COMPLETE = "security_scan_complete"
    CRUCIBLE_COMPLETE = "crucible_complete"


@dataclass(frozen=True, slots=True)
class PluginInfo:
    """Metadata for a loaded plugin."""

    name: str
    path: str
    enabled: bool
    description: str = ""


@dataclass(slots=True)
class PluginLoader:
    """Discover, load, and dispatch events to pipeline plugins.

    Plugins are Python files in ``factory/plugins/`` that expose an
    ``on_event(event: str, data: dict) -> None`` callable.  A manifest
    (``manifest.json``) controls enabled state and descriptions.
    """

    plugins_dir: Path = field(default=_PLUGINS_DIR)
    _plugins: dict[str, ModuleType] = field(default_factory=dict, init=False, repr=False)
    _info: dict[str, PluginInfo] = field(default_factory=dict, init=False, repr=False)

    def discover_plugins(self) -> list[PluginInfo]:
        """Scan plugins directory and return metadata for each plugin."""
        if not self.plugins_dir.is_dir():
            logger.debug("Plugins directory does not exist: %s", self.plugins_dir)
            return []

        manifest = self._read_manifest()
        result: list[PluginInfo] = []
        for py_file in sorted(self.plugins_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            name = py_file.stem
            entry = manifest.get(name, {})
            info = PluginInfo(
                name=name,
                path=str(py_file),
                enabled=bool(entry.get("enabled", True)),
                description=str(entry.get("description", "")),
            )
            result.append(info)
        return result

    def load_plugin(self, name: str) -> PluginInfo | None:
        """Load a single plugin by *name*.  Returns ``None`` on failure."""
        infos = {p.name: p for p in self.discover_plugins()}
        info = infos.get(name)
        if info is None:
            logger.warning("Plugin %r not found in %s", name, self.plugins_dir)
            return None
        if not info.enabled:
            logger.info("Plugin %r is disabled — skipping", name)
            return None
        return self._import_plugin(info)

    def load_all(self) -> list[PluginInfo]:
        """Discover and load every enabled plugin."""
        loaded: list[PluginInfo] = []
        for info in self.discover_plugins():
            if not info.enabled:
                logger.info("Plugin %r is disabled — skipping", info.name)
                continue
            result = self._import_plugin(info)
            if result is not None:
                loaded.append(result)
        if loaded:
            names = ", ".join(p.name for p in loaded)
            logger.info("Loaded %d plugin(s): %s", len(loaded), names)
        return loaded

    def emit(self, event: Event | str, data: dict[str, Any] | None = None) -> None:
        """Dispatch *event* to all loaded plugins.  Errors are logged, never propagated."""
        if not self._plugins:
            return
        event_name = event.value if isinstance(event, Event) else str(event)
        payload = data or {}
        for name, module in self._plugins.items():
            handler = getattr(module, "on_event", None)
            if handler is None:
                continue
            try:
                handler(event_name, payload)
            except Exception:
                logger.exception("Plugin %r failed for event %r", name, event_name)

    def loaded_plugins(self) -> list[PluginInfo]:
        """Return info for all currently loaded plugins."""
        return list(self._info.values())

    @property
    def plugin_count(self) -> int:
        """Number of currently loaded plugins."""
        return len(self._plugins)

    def _read_manifest(self) -> dict[str, Any]:
        """Read ``manifest.json``.  Returns empty dict if missing or malformed."""
        manifest_path = self.plugins_dir / _MANIFEST_NAME
        if not manifest_path.is_file():
            return {}
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            plugins_section: dict[str, Any] = raw.get("plugins", raw)
            return dict(plugins_section)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read plugin manifest: %s", exc)
            return {}

    def _import_plugin(self, info: PluginInfo) -> PluginInfo | None:
        """Import a plugin file and register it."""
        try:
            spec = importlib.util.spec_from_file_location(
                f"dark_factory.plugins.{info.name}", info.path,
            )
            if spec is None or spec.loader is None:
                logger.warning("Cannot create import spec for plugin %r", info.name)
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            if not hasattr(module, "on_event"):
                logger.warning("Plugin %r has no on_event() — skipping", info.name)
                return None
            self._plugins[info.name] = module
            self._info[info.name] = info
            logger.debug("Loaded plugin %r from %s", info.name, info.path)
            return info
        except Exception:
            logger.exception("Failed to load plugin %r", info.name)
            return None
