"""Dependency graph validator and Mermaid visualiser.

Reads ``modules_manifest.yaml``, builds a directed dependency graph,
detects cycles via ``graphlib.TopologicalSorter``, and emits Mermaid diagrams.
"""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

import yaml

_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "core" / "modules_manifest.yaml"
_MERMAID_OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "module-dependency-graph.mmd"


@dataclass(frozen=True, slots=True)
class GraphValidationResult:
    """Result of dependency graph validation."""

    passed: bool
    module_count: int
    edge_count: int
    topo_order: tuple[str, ...]
    issues: tuple[str, ...]


def _load_graph(manifest_path: Path | None = None) -> tuple[dict[str, list[str]], set[str]]:
    """Load the module manifest and return ``(graph, all_modules)``."""
    path = manifest_path or _MANIFEST_PATH
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    modules_raw: dict[str, Any] = raw.get("modules", {})
    graph: dict[str, list[str]] = {}
    all_modules: set[str] = set()
    for name, info in modules_raw.items():
        deps: list[str] = [str(d) for d in info.get("dependencies", [])]
        graph[name] = deps
        all_modules.add(name)
    return graph, all_modules


def validate(manifest_path: Path | None = None) -> GraphValidationResult:
    """Validate the dependency graph is acyclic and complete.

    Uses ``graphlib.TopologicalSorter`` (stdlib, Python 3.9+) for cycle
    detection.  Also checks that every dependency target is a declared module.
    """
    graph, all_modules = _load_graph(manifest_path)
    issues: list[str] = []
    edge_count = 0

    # Check for undefined dependencies.
    for name, deps in graph.items():
        for dep in deps:
            edge_count += 1
            if dep not in all_modules:
                issues.append(f"Module {name!r} depends on {dep!r} which is not declared")

    # Build TopologicalSorter (predecessors = dependencies).
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for name, deps in graph.items():
        valid_deps = [d for d in deps if d in all_modules]
        sorter.add(name, *valid_deps)

    # Attempt topological sort — detects cycles.
    topo_order: tuple[str, ...] = ()
    try:
        sorter.prepare()
        order: list[str] = []
        while sorter.is_active():
            ready = sorter.get_ready()
            order.extend(ready)
            sorter.done(*ready)
        topo_order = tuple(order)
    except CycleError as exc:
        issues.append(f"Cycle detected: {exc}")

    return GraphValidationResult(
        passed=len(issues) == 0,
        module_count=len(all_modules),
        edge_count=edge_count,
        topo_order=topo_order,
        issues=tuple(issues),
    )


def visualize(manifest_path: Path | None = None) -> str:
    """Generate a Mermaid diagram of the module dependency graph."""
    graph, _all_modules = _load_graph(manifest_path)
    lines: list[str] = ["graph TD"]
    for name in sorted(graph):
        safe_name = name.replace(".", "_")
        lines.append(f"    {safe_name}[{name}]")
    for name in sorted(graph):
        safe_src = name.replace(".", "_")
        for dep in sorted(graph[name]):
            safe_dst = dep.replace(".", "_")
            lines.append(f"    {safe_src} --> {safe_dst}")
    return "\n".join(lines) + "\n"


def write_mermaid(
    output_path: Path | None = None,
    manifest_path: Path | None = None,
) -> Path:
    """Write the Mermaid diagram to disk, creating parent dirs as needed."""
    dest = output_path or _MERMAID_OUTPUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(visualize(manifest_path), encoding="utf-8")
    return dest


def format_report(result: GraphValidationResult) -> str:
    """Render a human-readable validation report."""
    lines: list[str] = [
        "Dependency Graph Validation",
        "=" * 60,
        "",
        f"  Status : {'PASS' if result.passed else 'FAIL'}",
        f"  Modules: {result.module_count}",
        f"  Edges  : {result.edge_count}",
    ]
    if not result.passed:
        lines.append("")
        for issue in result.issues:
            lines.append(f"  - {issue}")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
