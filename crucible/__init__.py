"""Crucible test orchestrator — build, run, and evaluate end-to-end tests."""

from factory.crucible.orchestrator import CrucibleVerdict, run_crucible
from factory.crucible.repo_provision import (
    CrucibleRepoResult,
    manage_crucible_repo,
    provision_crucible_repo,
)
from factory.crucible.sharding import ShardResult, merge_verdicts, partition_tests
from factory.crucible.twin_runner import ScopeResult, TwinRunResult, run_crucible_twin

__all__ = [
    "CrucibleRepoResult",
    "CrucibleVerdict",
    "ScopeResult",
    "ShardResult",
    "TwinRunResult",
    "manage_crucible_repo",
    "merge_verdicts",
    "partition_tests",
    "provision_crucible_repo",
    "run_crucible",
    "run_crucible_twin",
]
