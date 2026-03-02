"""Learning orchestrator — run all 6 agents in dependency order or incrementally."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.learning.api_explorer import APIExplorerResult, run_api_explorer
from factory.learning.data_mapper import DataMapperResult, run_data_mapper
from factory.learning.domain_expert import DomainExpertResult, run_domain_expert
from factory.learning.integration_analyst import IntegrationResult, run_integration_analyst
from factory.learning.scout import ScoutResult, run_scout
from factory.learning.test_archaeologist import TestArchResult, run_test_archaeologist

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_STATE_DIR = Path(".dark-factory")

# File-classification patterns for incremental learning.
_CAT_PATTERNS: dict[str, re.Pattern[str]] = {
    "schema": re.compile(
        r"migrat|schema|\.sql$|prisma|drizzle|sequelize|typeorm|knex|alembic|flyway", re.I),
    "routes": re.compile(
        r"route|controller|handler|endpoint|api/|router|view|\.razor|\.cshtml", re.I),
    "tests": re.compile(
        r"test|spec|\.test\.|\.spec\.|__tests__|fixtures|e2e|cypress|playwright|jest|pytest", re.I),
    "structure": re.compile(
        r"package\.json|cargo\.toml|go\.mod|requirements\.txt|pyproject\.toml|gemfile"
        r"|pom\.xml|build\.gradle|makefile|dockerfile|docker-compose|\.github/"
        r"|\.gitlab-ci|jenkinsfile|tsconfig|webpack|vite\.config|\.env\.example", re.I),
    "integrations": re.compile(
        r"integrat|middleware|auth|oauth|plugin|adapter|connector|client|sdk"
        r"|webhook|queue|worker|job|cron|kafka|redis|rabbit|amqp|grpc|graphql", re.I),
    "domain": re.compile(
        r"model|entity|domain|aggregate|value.?object|enum|service|policy|rule", re.I),
}


@dataclass(frozen=True, slots=True)
class LearningResult:
    """Aggregated output of all learning agents."""

    scout: ScoutResult = field(default_factory=ScoutResult)
    api_explorer: APIExplorerResult = field(default_factory=APIExplorerResult)
    domain_expert: DomainExpertResult = field(default_factory=DomainExpertResult)
    data_mapper: DataMapperResult = field(default_factory=DataMapperResult)
    integration: IntegrationResult = field(default_factory=IntegrationResult)
    test_arch: TestArchResult = field(default_factory=TestArchResult)
    agents_run: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def _run_parallel(
    tasks: dict[str, Callable[[], Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Run callables in parallel. Returns (results_dict, agents_run, errors)."""
    results: dict[str, Any] = {}
    agents_run: list[str] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                obj = fut.result()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                agents_run.append(name)
                continue
            results[name] = obj
            agents_run.append(name)
            for e in getattr(obj, "errors", ()):
                errors.append(f"{name}: {e}")
    return results, agents_run, errors


def _save_knowledge(result: LearningResult, repo_name: str, *, state_dir: Path | None = None) -> Path:
    """Persist aggregated knowledge to knowledge.json."""
    sd = (state_dir or _STATE_DIR) / "learning" / repo_name
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "knowledge.json"
    payload: dict[str, object] = {}
    for key in ("scout", "api_explorer", "domain_expert", "data_mapper", "integration", "test_arch"):
        agent_data = asdict(getattr(result, key))
        agent_data.pop("raw_output", None)
        payload[key] = agent_data
    payload["agents_run"] = list(result.agents_run)
    payload["errors"] = list(result.errors)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Knowledge saved to %s", out)
    return out


def _build_result(
    scout: ScoutResult, results: dict[str, Any],
    agents_run: list[str], errors: list[str],
    workspace: Workspace, *, state_dir: Path | None = None,
) -> LearningResult:
    lr = LearningResult(
        scout=scout,
        api_explorer=results.get("api_explorer", APIExplorerResult()),
        domain_expert=results.get("domain_expert", DomainExpertResult()),
        data_mapper=results.get("data_mapper", DataMapperResult()),
        integration=results.get("integration_analyst", IntegrationResult()),
        test_arch=results.get("test_archaeologist", TestArchResult()),
        agents_run=tuple(agents_run), errors=tuple(errors),
    )
    repo_name = workspace.name or Path(workspace.path).name
    _save_knowledge(lr, repo_name, state_dir=state_dir)
    return lr


def run_full_learning(
    workspace: Workspace,
    *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> LearningResult:
    """Run all 6 learning agents in dependency order.

    Phase 1: Scout (blocking)
    Phase 2: API Explorer + Domain Expert + Data Mapper (parallel)
    Phase 3: Integration Analyst + Test Archaeologist (parallel)
    """
    agents_run: list[str] = []
    errors: list[str] = []
    kw: dict[str, Any] = {"invoke_fn": invoke_fn, "state_dir": state_dir}

    # Phase 1: Scout
    logger.info("Phase 1: running Scout agent")
    scout = run_scout(workspace, **kw)
    agents_run.append("scout")
    errors.extend(f"scout: {e}" for e in scout.errors)

    # Phase 2: API Explorer + Domain Expert + Data Mapper (parallel)
    logger.info("Phase 2: running API Explorer, Domain Expert, Data Mapper in parallel")
    p2_tasks: dict[str, Callable[[], Any]] = {
        "api_explorer": lambda: run_api_explorer(workspace, scout, **kw),
        "domain_expert": lambda: run_domain_expert(workspace, scout, **kw),
        "data_mapper": lambda: run_data_mapper(workspace, scout, **kw),
    }
    p2_results, p2_run, p2_err = _run_parallel(p2_tasks)
    agents_run.extend(p2_run)
    errors.extend(p2_err)
    api = p2_results.get("api_explorer", APIExplorerResult())

    # Phase 3: Integration Analyst + Test Archaeologist (parallel)
    logger.info("Phase 3: running Integration Analyst, Test Archaeologist in parallel")
    p3_tasks: dict[str, Callable[[], Any]] = {
        "integration_analyst": lambda: run_integration_analyst(workspace, api, **kw),
        "test_archaeologist": lambda: run_test_archaeologist(workspace, scout, **kw),
    }
    p3_results, p3_run, p3_err = _run_parallel(p3_tasks)
    agents_run.extend(p3_run)
    errors.extend(p3_err)

    all_results = {**p2_results, **p3_results}
    return _build_result(scout, all_results, agents_run, errors, workspace, state_dir=state_dir)


def _classify_changes(changed_files: list[Path]) -> dict[str, bool]:
    """Classify changed files into categories that map to learning agents."""
    cats = {k: False for k in _CAT_PATTERNS}
    for fp in changed_files:
        s = str(fp)
        for cat, pat in _CAT_PATTERNS.items():
            if pat.search(s):
                cats[cat] = True
    return cats


def run_incremental_learning(
    workspace: Workspace,
    changed_files: list[Path],
    *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> LearningResult:
    """Re-run only affected agents based on which files changed.

    Falls back to full learning if structural changes detected or no files given.
    """
    if not changed_files:
        return run_full_learning(workspace, invoke_fn=invoke_fn, state_dir=state_dir)

    cats = _classify_changes(changed_files)
    if cats["structure"]:
        logger.info("Structural changes detected — running full learning")
        return run_full_learning(workspace, invoke_fn=invoke_fn, state_dir=state_dir)

    agents_run: list[str] = []
    errors: list[str] = []
    kw: dict[str, Any] = {"invoke_fn": invoke_fn, "state_dir": state_dir}

    # Scout always runs as a dependency for downstream agents
    logger.info("Incremental: running Scout agent")
    scout = run_scout(workspace, **kw)
    agents_run.append("scout")
    errors.extend(f"scout: {e}" for e in scout.errors)

    # Phase 2: only affected agents
    p2_tasks: dict[str, Callable[[], Any]] = {}
    if cats["routes"]:
        p2_tasks["api_explorer"] = lambda: run_api_explorer(workspace, scout, **kw)
    if cats["domain"]:
        p2_tasks["domain_expert"] = lambda: run_domain_expert(workspace, scout, **kw)
    if cats["schema"]:
        p2_tasks["data_mapper"] = lambda: run_data_mapper(workspace, scout, **kw)

    p2_results: dict[str, Any] = {}
    if p2_tasks:
        logger.info("Incremental phase 2: %s", ", ".join(p2_tasks))
        p2_results, p2_run, p2_err = _run_parallel(p2_tasks)
        agents_run.extend(p2_run)
        errors.extend(p2_err)

    api = p2_results.get("api_explorer", APIExplorerResult())

    # Phase 3: only affected agents
    p3_tasks: dict[str, Callable[[], Any]] = {}
    if cats["integrations"] or cats["routes"]:
        p3_tasks["integration_analyst"] = lambda: run_integration_analyst(workspace, api, **kw)
    if cats["tests"]:
        p3_tasks["test_archaeologist"] = lambda: run_test_archaeologist(workspace, scout, **kw)

    p3_results: dict[str, Any] = {}
    if p3_tasks:
        logger.info("Incremental phase 3: %s", ", ".join(p3_tasks))
        p3_results, p3_run, p3_err = _run_parallel(p3_tasks)
        agents_run.extend(p3_run)
        errors.extend(p3_err)

    all_results = {**p2_results, **p3_results}
    return _build_result(scout, all_results, agents_run, errors, workspace, state_dir=state_dir)
