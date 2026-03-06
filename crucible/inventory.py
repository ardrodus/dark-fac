"""Scenario inventory — traverse the crucible repo and build a manifest.

Called as a shell node in crucible.dot after cloning the crucible repo.
Produces scenario-manifest.json consumed by downstream pipeline agents.

Usage (from DOT shell node)::

    python -m dark_factory.crucible.inventory <crucible_tests_path>

Or as a library call::

    from dark_factory.crucible.inventory import inventory_scenarios
    manifest = inventory_scenarios(Path("/path/to/crucible-tests"))
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def inventory_scenarios(crucible_path: Path) -> dict:
    """Traverse crucible repo and build a scenario manifest.

    Recursively finds all ``*.scenario`` files under ``scenarios/``,
    skipping the ``_example/`` directory. Classifies each as either
    ``graduated`` (permanent) or ``pr`` (new, pending graduation)
    based on filename prefix.

    Returns a dict written to ``<crucible_path>/scenario-manifest.json``::

        {
            "total": 5,
            "graduated": [
                {"path": "scenarios/auth/login.scenario", "feature": "auth"},
                ...
            ],
            "pr": [
                {"path": "scenarios/pr-42-new-endpoint.scenario", "pr_number": "42"},
                ...
            ]
        }
    """
    scenarios_dir = crucible_path / "scenarios"
    graduated: list[dict[str, str]] = []
    pr_scenarios: list[dict[str, str]] = []

    if scenarios_dir.is_dir():
        for scenario_file in sorted(scenarios_dir.rglob("*.scenario")):
            # Skip example directory
            rel = scenario_file.relative_to(crucible_path)
            if "_example" in rel.parts:
                continue

            rel_str = str(rel).replace("\\", "/")
            name = scenario_file.stem

            if name.startswith("pr-"):
                # Extract PR number: pr-42-feature → "42"
                parts = name.split("-", 2)
                pr_num = parts[1] if len(parts) >= 2 else ""  # noqa: PLR2004
                pr_scenarios.append({"path": rel_str, "pr_number": pr_num})
            else:
                # Feature = parent directory name (or "root" if in scenarios/)
                feature = scenario_file.parent.name
                if feature == "scenarios":
                    feature = "root"
                graduated.append({"path": rel_str, "feature": feature})

    manifest = {
        "total": len(graduated) + len(pr_scenarios),
        "graduated": graduated,
        "pr": pr_scenarios,
    }

    manifest_path = crucible_path / "scenario-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return manifest


def main() -> None:
    """CLI entry point for DOT shell node invocation."""
    if len(sys.argv) < 2:  # noqa: PLR2004
        sys.stderr.write("Usage: python -m dark_factory.crucible.inventory <crucible_tests_path>\n")
        raise SystemExit(1)

    crucible_path = Path(sys.argv[1])
    if not crucible_path.is_dir():
        sys.stderr.write(f"Not a directory: {crucible_path}\n")
        raise SystemExit(1)

    manifest = inventory_scenarios(crucible_path)
    sys.stdout.write(f"Inventoried {manifest['total']} scenarios "
                     f"({len(manifest['graduated'])} graduated, "
                     f"{len(manifest['pr'])} PR-pending)\n")


if __name__ == "__main__":
    main()
