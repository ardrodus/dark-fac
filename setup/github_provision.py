"""GitHub provisioning — auth check only.

Validates that the GitHub CLI is authenticated and can access the
configured repository.  Org/repo provisioning (labels, workflows,
secrets, branch protection) has been removed.
"""
from __future__ import annotations

import logging

from factory.integrations.shell import gh

logger = logging.getLogger(__name__)


def check_github_auth() -> bool:
    """Return ``True`` if ``gh auth status`` succeeds.

    This is the simplified replacement for the former ``provision_github``
    function, which created labels, workflows, secrets, and branch
    protection.  Now we only verify that the CLI is authenticated.
    """
    result = gh(["auth", "status"], timeout=15)
    if result.returncode == 0:
        logger.info("GitHub CLI authenticated")
        return True
    logger.warning("GitHub CLI not authenticated — run: gh auth login")
    return False
