"""Command handler implementations for the dark-factory CLI.

Each handler encapsulates the core logic for one CLI command.
They are called by both the Click commands (backward compatibility)
and the argparse-based dispatch path.
"""

from __future__ import annotations

import sys


def run_doctor(*, migration: bool, modules: bool, debug_modules: bool, deps: bool = False, lint: bool = False) -> None:
    """Execute the ``doctor`` command logic.

    Parameters
    ----------
    migration:
        Show bash→Python migration progress.
    modules:
        Validate the module manifest.
    debug_modules:
        Show which modules loaded and when.
    deps:
        Validate the module dependency graph.
    lint:
        Run file size compliance check.
    """
    if lint:
        from factory.tools import lint_file_sizes

        lint_result = lint_file_sizes.validate()
        sys.stdout.write(lint_file_sizes.format_report(lint_result) + "\n")
        if not lint_result.passed:
            raise SystemExit(1)
    elif deps:
        from factory.tools import dependency_graph as depgraph

        dep_result = depgraph.validate()
        sys.stdout.write(depgraph.format_report(dep_result) + "\n")
        if not dep_result.passed:
            raise SystemExit(1)
    elif migration:
        from factory.core.migration import format_report, migration_report

        report = migration_report()
        sys.stdout.write(format_report(report) + "\n")
    elif modules:
        from factory.core.module_loader import (
            ModuleRegistry,
            format_validation_report,
            load_manifest,
        )

        registry = ModuleRegistry()
        load_manifest(registry)
        mod_result = registry.validate_manifest()
        sys.stdout.write(format_validation_report(mod_result) + "\n")
        if not mod_result.passed:
            raise SystemExit(1)
    elif debug_modules:
        from factory.core.module_loader import (
            ModuleRegistry,
            format_debug_report,
            load_manifest,
        )

        registry = ModuleRegistry()
        load_manifest(registry)
        registry.startup()
        sys.stdout.write(format_debug_report(registry) + "\n")
    else:
        sys.stdout.write("dark-factory doctor: all checks passed.\n")


def run_selftest() -> None:
    """Execute the ``selftest`` command logic.

    Runs all built-in validators (module manifest + dependency graph)
    and reports a combined pass/fail status.
    """
    from factory.core.module_loader import (
        ModuleRegistry,
        format_validation_report,
        load_manifest,
    )
    from factory.tools import dependency_graph as depgraph

    all_passed = True

    # 1) Module manifest validation
    registry = ModuleRegistry()
    load_manifest(registry)
    manifest_result = registry.validate_manifest()
    sys.stdout.write(format_validation_report(manifest_result) + "\n\n")
    if not manifest_result.passed:
        all_passed = False

    # 2) Dependency graph validation
    graph_result = depgraph.validate()
    sys.stdout.write(depgraph.format_report(graph_result) + "\n")
    if not graph_result.passed:
        all_passed = False

    if all_passed:
        sys.stdout.write("\nselftest: PASS\n")
    else:
        sys.stdout.write("\nselftest: FAIL\n")
        raise SystemExit(1)


def run_status(*, epics: bool, bootstrap: bool) -> None:
    """Execute the ``status`` command logic.

    Parameters
    ----------
    epics:
        Show epic-level progress.
    bootstrap:
        Show bootstrap pipeline status.
    """
    from factory.ui.status_reporter import (
        show_bootstrap_status,
        show_epic_status,
        show_status,
    )

    if epics:
        sys.stdout.write(show_epic_status() + "\n")
    elif bootstrap:
        sys.stdout.write(show_bootstrap_status() + "\n")
    else:
        sys.stdout.write(show_status() + "\n")


def run_gates(*, run_all: bool, list_gates: bool, run_name: str) -> None:
    """Execute the ``gates`` command logic.

    Parameters
    ----------
    run_all:
        Run every discovered gate in sequence.
    list_gates:
        List all discovered gates with their check counts.
    run_name:
        Run a single gate by name.
    """
    from factory.gates.discovery import (
        discover_gates,
        format_gate_list,
        format_unified_report,
        run_all_gates,
        run_gate_by_name,
    )

    if list_gates:
        gates = discover_gates()
        sys.stdout.write(format_gate_list(gates) + "\n")
    elif run_name:
        try:
            report = run_gate_by_name(run_name)
        except KeyError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            raise SystemExit(1) from None
        from factory.gates.discovery import UnifiedReport

        unified = UnifiedReport(gate_reports=(report,))
        sys.stdout.write(format_unified_report(unified) + "\n")
        if not report.passed:
            raise SystemExit(1)
    elif run_all:
        unified = run_all_gates()
        sys.stdout.write(format_unified_report(unified) + "\n")
        if not unified.overall_passed:
            raise SystemExit(1)
    else:
        sys.stdout.write("Usage: dark-factory gates [--run-all | --list | --run <name>]\n")


def run_smoke_test(*, title: str) -> None:
    """Execute the ``smoke-test`` command logic.

    Parameters
    ----------
    title:
        Story title for the smoke test.
    """
    from factory.pipeline.runner import StoryContext, run_pipeline

    story = StoryContext(
        title=title,
        description="Smoke test: verify the pipeline processes a Python story.",
        acceptance_criteria=("Pipeline completes all six stages.",),
        changed_files=("factory/pipeline/runner.py",),
    )
    result = run_pipeline(story)

    for stage_result in result.stages:
        status = "PASS" if stage_result.passed else "FAIL"
        sys.stdout.write(
            f"  [{status}] {stage_result.stage.value}: {stage_result.detail}\n"
        )

    if result.passed:
        sys.stdout.write("smoke-test: PASS\n")
    else:
        sys.stdout.write("smoke-test: FAIL\n")
        raise SystemExit(1)
