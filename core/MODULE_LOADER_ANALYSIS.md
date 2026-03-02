# Module Loader Analysis — US-018

## Summary

The module loader was **simplified from 331 lines to 139 lines** (58% reduction).
Standard Python imports suffice for all runtime module loading. The YAML manifest
and validation/reporting functions are retained for the `doctor` and `selftest`
diagnostic CLI commands.

## What Was Removed

| Component | Lines | Reason |
|-----------|-------|--------|
| `LazyModuleProxy` class | 27 | Zero external callers — no code uses lazy proxies |
| `ModuleEntry` frozen dataclass | 12 | Over-engineered; replaced by reading YAML dicts directly |
| `ValidationResult` dataclass | 5 | Replaced by simple `(passed, issues, count)` tuple |
| `ModuleRegistry` class | 120 | Full OOP registry unnecessary — all 22 modules use standard imports |
| `CircularDependencyError` class | 5 | Cycle detection now reports via issues list, no need for custom exception |
| `load_manifest()` function | 12 | Replaced by `_read_modules()` (reads YAML directly, no registry) |

## What Was Kept

| Component | Purpose |
|-----------|---------|
| `validate_manifest()` | Checks for undefined deps + dependency cycles (used by `doctor --modules`, `selftest`) |
| `format_validation_report()` | Human-readable validation output |
| `format_debug_report()` | Imports core modules, times them, prints load report (used by `doctor --debug-modules`) |

## Modules That Need Lazy Loading

**None.** All 22 modules in the manifest use standard Python imports at their call sites
(e.g., `from factory.pipeline.runner import run_pipeline`). The lazy loading infrastructure
(`LazyModuleProxy`, `require()`, `get_proxy()`) had zero external callers.

## Modules That Use Standard Imports

All 22 modules in the manifest. The codebase consistently uses Python's built-in import
system with deferred imports inside functions where needed (e.g., in `handlers.py`).

## Manifest Status

`modules_manifest.yaml` updated: removed self-referential `factory.core.module_loader`
entry (the loader doesn't need to register itself). 22 modules remain, all with valid
import paths and no undefined dependencies or cycles.
