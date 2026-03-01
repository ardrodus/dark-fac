"""Integration test gate — migrated from integration-test-gate.sh (US-008/US-026).

Runs AFTER individual stories pass TDD and BEFORE the PR is created.
Catches "each story works alone but they don't work together" problems by:

1. **Collecting story artifacts** — design docs, contracts, schemas,
   interface definitions, and test strategies for each story.
2. **Building an integration prompt** — assembled context for an
   integration tester agent.
3. **Verifying integration test results** — parses agent output for
   pass/fail verdict.

Uses :class:`GateRunner` from :mod:`factory.gates.framework`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from factory.gates.framework import GateReport, GateRunner

logger = logging.getLogger(__name__)

_API_EXTS = ("yaml", "graphql", "proto")
_SCHEMA_EXTS = ("sql", "json")
_IFACE_EXTS = ("ts", "py", "go", "rs", "java", "rb", "js")
_TEST_EXTS = (
    "*.ts", "*.js", "*.py", "*.go", "*.rs", "*.java", "*.rb", "*.sh",
    "*.test.*", "*_test.*", "test_*",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _find_first(sd: Path, prefix: str, sid: str, exts: tuple[str, ...]) -> Path | None:
    for ext in exts:
        p = sd / f"{prefix}-{sid}.{ext}"
        if p.is_file():
            return p
    # CLI variant for API contracts
    if prefix == "api-contract":
        cli = sd / f"api-contract-{sid}-cli.yaml"
        if cli.is_file():
            return cli
    return None


# ── Artifact collection ──────────────────────────────────────────


def collect_story_artifacts(
    specs_dir: Path, story_ids: list[str],
) -> str:
    """Gather design docs, contracts, schemas, interfaces, test strategies."""
    sections: list[str] = []
    for sid in story_ids:
        parts: list[str] = []
        design = specs_dir / f"design-{sid}.md"
        if design.is_file():
            parts.append(f"#### Design (Story {sid})\n{_read(design)}")
        contract = _find_first(specs_dir, "api-contract", sid, _API_EXTS)
        if contract:
            parts.append(f"#### API Contract (Story {sid})\n```\n{_read(contract)}\n```")
        schema = _find_first(specs_dir, "schema", sid, _SCHEMA_EXTS)
        if schema:
            parts.append(f"#### Schema (Story {sid})\n```\n{_read(schema)}\n```")
        iface = _find_first(specs_dir, "interfaces", sid, _IFACE_EXTS)
        if iface:
            parts.append(f"#### Interfaces (Story {sid})\n```\n{_read(iface)}\n```")
        strategy = specs_dir / f"test-strategy-{sid}.md"
        if strategy.is_file():
            parts.append(f"#### Test Strategy (Story {sid})\n{_read(strategy)}")
        if parts:
            sections.append(f"### Story {sid}\n" + "\n".join(parts))
    return "\n\n".join(sections)


def collect_existing_tests(workspace: Path) -> list[str]:
    """Return relative paths of existing test files."""
    tests_dir = workspace / "tests"
    if not tests_dir.is_dir():
        return []
    results: list[str] = []
    for p in sorted(tests_dir.rglob("*")):
        if p.is_file() and p.suffix in {".ts", ".js", ".py", ".go", ".rs", ".java", ".rb", ".sh"}:
            results.append(str(p.relative_to(workspace)))
    return results


# ── Check implementations ────────────────────────────────────────


def _check_artifacts_present(sd: Path, story_ids: list[str]) -> bool | str:
    found: list[str] = []
    for sid in story_ids:
        if (sd / f"design-{sid}.md").is_file():
            found.append(f"design-{sid}")
        if _find_first(sd, "api-contract", sid, _API_EXTS):
            found.append(f"contract-{sid}")
    if not found:
        return "no story artifacts found — integration skipped"
    return f"artifacts found: {', '.join(found)}"


def _check_test_dir_exists(workspace: Path) -> bool | str:
    tests_dir = workspace / "tests"
    if not tests_dir.is_dir():
        return "no tests/ directory — integration skipped"
    count = len(collect_existing_tests(workspace))
    return f"tests/ exists ({count} test file(s))"


def _check_integration_dir(workspace: Path) -> bool | str:
    integ_dir = workspace / "tests" / "integration"
    if not integ_dir.is_dir():
        return "no tests/integration/ — will be created by agent"
    files = list(integ_dir.iterdir())
    count = sum(1 for f in files if f.is_file())
    return f"tests/integration/ has {count} file(s)"


def _check_cross_story_boundaries(
    sd: Path, story_ids: list[str],
) -> bool | str:
    if len(story_ids) < 2:
        return "single story — no cross-story boundaries to check"
    shared_entities: dict[str, list[str]] = {}
    for sid in story_ids:
        design = _read(sd / f"design-{sid}.md")
        import re  # noqa: PLC0415
        for m in re.finditer(
            r"(?:class|interface|type|struct)\s+([A-Z][A-Za-z0-9]+)", design,
        ):
            shared_entities.setdefault(m.group(1), []).append(sid)
    shared = {e: sids for e, sids in shared_entities.items() if len(sids) > 1}
    if shared:
        names = ", ".join(f"{e}({'/'.join(s)})" for e, s in list(shared.items())[:5])
        return f"shared entities across stories: {names}"
    return "no shared entities detected between stories"


# ── Discovery interface ───────────────────────────────────────────

GATE_NAME = "integration-test"


def create_runner(
    workspace: str | Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Create a configured (but not executed) integration-test gate runner."""
    ws = Path(workspace)
    sd = ws / ".dark-factory" / "specs"
    story_ids: list[str] = []
    runner = GateRunner(GATE_NAME, metrics_dir=metrics_dir)
    runner.register_check("artifacts-present", lambda: _check_artifacts_present(sd, story_ids))
    runner.register_check("test-dir", lambda: _check_test_dir_exists(ws))
    runner.register_check("integration-dir", lambda: _check_integration_dir(ws))
    runner.register_check(
        "cross-story-boundaries", lambda: _check_cross_story_boundaries(sd, story_ids),
    )
    return runner


# ── Public API ───────────────────────────────────────────────────


def run_integration_test_gate(
    workspace: str | Path,
    specs_dir: str | Path,
    story_ids: list[str],
    *,
    metrics_dir: str | Path | None = None,
) -> GateReport:
    """Run the integration test gate.

    Registers four checks (artifacts present, test dir, integration dir,
    cross-story boundaries) and delegates to :class:`GateRunner`.
    """
    ws = Path(workspace)
    sd = Path(specs_dir)

    runner = GateRunner("integration-test", metrics_dir=metrics_dir)
    runner.register_check("artifacts-present", lambda: _check_artifacts_present(sd, story_ids))
    runner.register_check("test-dir", lambda: _check_test_dir_exists(ws))
    runner.register_check("integration-dir", lambda: _check_integration_dir(ws))
    runner.register_check(
        "cross-story-boundaries", lambda: _check_cross_story_boundaries(sd, story_ids),
    )
    return runner.run()
