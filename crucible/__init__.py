"""Crucible test orchestrator — build, run, and evaluate end-to-end tests."""

from dark_factory.crucible.orchestrator import CrucibleVerdict, run_crucible, run_sharded_crucible
from dark_factory.crucible.repo_provision import (
    CrucibleRepoResult,
    manage_crucible_repo,
    provision_crucible_repo,
)
from dark_factory.crucible.sharding import ShardResult, merge_verdicts, partition_tests

__all__ = [
    "CrucibleRepoResult",
    "CrucibleVerdict",
    "ShardResult",
    "manage_crucible_repo",
    "merge_verdicts",
    "partition_tests",
    "provision_crucible_repo",
    "run_crucible",
    "run_sharded_crucible",
]
