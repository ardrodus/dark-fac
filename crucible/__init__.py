"""Crucible — test validation subsystem.

The pipeline logic lives in pipelines/crucible.dot.  This package
provides the repo-provisioning helper used during onboarding.
"""

from dark_factory.crucible.repo_provision import (
    CrucibleRepoResult,
    manage_crucible_repo,
    provision_crucible_repo,
)

__all__ = [
    "CrucibleRepoResult",
    "manage_crucible_repo",
    "provision_crucible_repo",
]
