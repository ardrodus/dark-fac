"""Lazy, dependency-aware module loader with cycle detection and manifest validation."""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

if TYPE_CHECKING:
    from types import ModuleType

logger = logging.getLogger(__name__)

_MODULES_MANIFEST_PATH = Path(__file__).resolve().parent / "modules_manifest.yaml"

LoadTag = Literal["core", "deferred"]


class CircularDependencyError(Exception):
    """Raised when a circular dependency is detected during module loading."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        chain = " \u2192 ".join(cycle)
        super().__init__(f"Circular dependency detected: {chain}")


@dataclass(frozen=True, slots=True)
class ModuleEntry:
    """Registry entry for a lazily-loaded module."""

    name: str
    module_path: str
    loaded: bool
    load_time_ms: float
    module: ModuleType | None
    dependencies: tuple[str, ...] = ()
    load_tag: LoadTag = "deferred"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of manifest validation."""

    passed: bool
    issues: tuple[str, ...]
    module_count: int


class LazyModuleProxy:
    """Transparent proxy that triggers ``require()`` on first attribute access.

    Callers interact with the proxy as if it were the real module.
    The first attribute lookup imports the module and replaces the proxy
    reference inside the registry.
    """

    def __init__(self, registry: ModuleRegistry, name: str) -> None:
        object.__setattr__(self, "_lmp_registry", registry)
        object.__setattr__(self, "_lmp_name", name)

    def _resolve(self) -> ModuleType:
        registry: ModuleRegistry = object.__getattribute__(self, "_lmp_registry")
        name: str = object.__getattribute__(self, "_lmp_name")
        return registry.require(name)

    def __getattr__(self, attr: str) -> Any:
        module = self._resolve()
        return getattr(module, attr)

    def __repr__(self) -> str:
        name: str = object.__getattribute__(self, "_lmp_name")
        registry: ModuleRegistry = object.__getattribute__(self, "_lmp_registry")
        loaded = registry.is_loaded(name)
        tag = "loaded" if loaded else "deferred"
        return f"<LazyModuleProxy {name!r} [{tag}]>"


class ModuleRegistry:
    """Lazy, dependency-aware module registry with cycle detection.

    Modules are registered by name with a dotted import path.  They are
    only imported when :meth:`require` is called, and each module is
    imported at most once.
    """

    def __init__(self) -> None:
        self._registry: dict[str, ModuleEntry] = {}
        self._loading_stack: list[str] = []
        self._load_log: list[tuple[str, float, str]] = []

    def register(
        self,
        name: str,
        module_path: str,
        *,
        dependencies: tuple[str, ...] = (),
        load_tag: LoadTag = "deferred",
    ) -> None:
        """Register a module by *name* with its dotted import path.

        The module is **not** imported at registration time.
        """
        self._registry[name] = ModuleEntry(
            name=name,
            module_path=module_path,
            loaded=False,
            load_time_ms=0.0,
            module=None,
            dependencies=dependencies,
            load_tag=load_tag,
        )
        logger.debug("Registered module %r \u2192 %s (load=%s)", name, module_path, load_tag)

    def require(self, name: str) -> ModuleType:
        """Import the module if not already loaded; return it.

        Raises
        ------
        KeyError
            If *name* has not been registered.
        CircularDependencyError
            If requiring *name* would create a dependency cycle.
        """
        if name not in self._registry:
            msg = f"Module {name!r} is not registered"
            raise KeyError(msg)

        entry = self._registry[name]
        if entry.loaded:
            assert entry.module is not None  # noqa: S101
            return entry.module

        if name in self._loading_stack:
            cycle_start = self._loading_stack.index(name)
            cycle = [*self._loading_stack[cycle_start:], name]
            raise CircularDependencyError(cycle)

        self._loading_stack.append(name)
        try:
            start = time.monotonic()
            module = importlib.import_module(entry.module_path)
            duration_ms = (time.monotonic() - start) * 1000

            self._registry[name] = ModuleEntry(
                name=name,
                module_path=entry.module_path,
                loaded=True,
                load_time_ms=duration_ms,
                module=module,
                dependencies=entry.dependencies,
                load_tag=entry.load_tag,
            )
            phase = "startup" if entry.load_tag == "core" else "on-demand"
            self._load_log.append((name, duration_ms, phase))
            logger.debug("Loaded module %r (%.1f ms, %s)", name, duration_ms, phase)
            return module
        finally:
            self._loading_stack.remove(name)

    def startup(self) -> float:
        """Import all core modules. Return total startup time in ms.

        Deferred modules are skipped — they will be loaded on first use
        via :class:`LazyModuleProxy` or an explicit :meth:`require` call.
        """
        total_start = time.monotonic()
        for name, entry in list(self._registry.items()):
            if entry.load_tag == "core" and not entry.loaded:
                self.require(name)
        total_ms = (time.monotonic() - total_start) * 1000
        logger.info("Startup: loaded %d core modules in %.1f ms", self.core_count(), total_ms)
        return total_ms

    def get_proxy(self, name: str) -> LazyModuleProxy:
        """Return a :class:`LazyModuleProxy` for the named module."""
        if name not in self._registry:
            msg = f"Module {name!r} is not registered"
            raise KeyError(msg)
        return LazyModuleProxy(self, name)

    def is_loaded(self, name: str) -> bool:
        """Return ``True`` if *name* has been loaded, ``False`` otherwise."""
        if name not in self._registry:
            return False
        return self._registry[name].loaded

    def list_modules(self) -> dict[str, ModuleEntry]:
        """Return all registered modules and their load status."""
        return dict(self._registry)

    def core_count(self) -> int:
        """Return the number of core modules."""
        return sum(1 for e in self._registry.values() if e.load_tag == "core")

    def deferred_count(self) -> int:
        """Return the number of deferred modules."""
        return sum(1 for e in self._registry.values() if e.load_tag == "deferred")

    def load_log(self) -> list[tuple[str, float, str]]:
        """Return the chronological log of ``(name, ms, phase)`` tuples."""
        return list(self._load_log)

    def validate_manifest(self) -> ValidationResult:
        """Validate the registry for undefined dependencies and cycles.

        Returns a :class:`ValidationResult` with any issues found.
        """
        issues: list[str] = []

        # Check for undefined dependencies
        for name, entry in self._registry.items():
            for dep in entry.dependencies:
                if dep not in self._registry:
                    issues.append(
                        f"Module {name!r} depends on {dep!r} which is not registered"
                    )

        # Check for cycles using DFS (white=0, gray=1, black=2)
        white, gray, black = 0, 1, 2
        color: dict[str, int] = {n: white for n in self._registry}
        path: list[str] = []

        def _dfs(node: str) -> None:
            color[node] = gray
            path.append(node)
            for dep in self._registry[node].dependencies:
                if dep not in self._registry:
                    continue  # already reported as undefined
                if color[dep] == gray:
                    cycle_start = path.index(dep)
                    cycle = [*path[cycle_start:], dep]
                    chain = " \u2192 ".join(cycle)
                    issues.append(f"Circular dependency: {chain}")
                elif color[dep] == white:
                    _dfs(dep)
            path.pop()
            color[node] = black

        for name in self._registry:
            if color[name] == white:
                _dfs(name)

        return ValidationResult(
            passed=len(issues) == 0,
            issues=tuple(issues),
            module_count=len(self._registry),
        )


def load_manifest(
    registry: ModuleRegistry,
    path: Path | None = None,
) -> None:
    """Read the modules manifest and register every module in *registry*.

    Parameters
    ----------
    registry:
        The :class:`ModuleRegistry` to populate.
    path:
        Override the manifest location (useful for testing).
    """
    manifest_path = path or _MODULES_MANIFEST_PATH
    raw: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    modules_raw: dict[str, Any] = raw.get("modules", {})
    for name, info in modules_raw.items():
        module_path = str(info.get("path", name))
        deps_raw: list[str] = info.get("dependencies", [])
        dependencies = tuple(str(d) for d in deps_raw)
        load_tag: LoadTag = info.get("load", "deferred")
        registry.register(name, module_path, dependencies=dependencies, load_tag=load_tag)


def format_validation_report(result: ValidationResult) -> str:
    """Render a human-readable validation report."""
    lines: list[str] = []
    lines.append("Module Manifest Validation")
    lines.append("=" * 60)
    lines.append("")
    if result.passed:
        lines.append("  Status  : PASS")
        lines.append(f"  Modules : {result.module_count}")
    else:
        lines.append(f"  Status  : FAIL \u2014 {len(result.issues)} issue(s) found")
        lines.append(f"  Modules : {result.module_count}")
        lines.append("")
        for issue in result.issues:
            lines.append(f"  - {issue}")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def format_debug_report(registry: ModuleRegistry) -> str:
    """Render a debug report showing which modules loaded and when."""
    lines: list[str] = []
    lines.append("Module Load Report")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Core modules    : {registry.core_count()}")
    lines.append(f"  Deferred modules: {registry.deferred_count()}")
    lines.append("")
    lines.append("  Loaded modules:")
    log = registry.load_log()
    if log:
        for name, ms, phase in log:
            lines.append(f"    [{phase:>9}] {name:<40} {ms:6.1f} ms")
    else:
        lines.append("    (none)")
    lines.append("")
    lines.append("  Deferred (not yet loaded):")
    modules = registry.list_modules()
    deferred_unloaded = [
        n for n, e in modules.items()
        if e.load_tag == "deferred" and not e.loaded
    ]
    if deferred_unloaded:
        for name in deferred_unloaded:
            lines.append(f"    {name}")
    else:
        lines.append("    (all loaded)")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
