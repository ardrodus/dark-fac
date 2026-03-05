"""GitHub provisioning — labels, CI workflow, issue template, branch protection.

Creates the scaffolding a target repo needs for Dark Factory to operate:
labels for pipeline transitions, a CI workflow, an issue template, and
branch protection on ``main``.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── Label definitions ─────────────────────────────────────────────

LABELS: tuple[tuple[str, str, str], ...] = (
    # (name, hex_color, description)
    ("factory-task", "0E8A16", "Dark Factory managed task"),
    ("backlog", "C2E0C6", "New, ready for architecture review"),
    ("arch-review", "FBCA04", "Architecture pipeline is reviewing"),
    ("arch-approved", "0075CA", "Pipeline approved, ready for engineer"),
    ("in-progress", "D93F0B", "Assigned to a TDD pipeline"),
    ("in-review", "BFD4F2", "PR open, CI running"),
    ("completed", "0E8A16", "Merged to main"),
    ("human-review", "B60205", "Agent failed, needs human"),
    ("deployed-dev", "1D76DB", "Deployed to dev, health check passed"),
    ("deploy-failed", "B60205", "Deploy to dev failed"),
    ("crucible-passed", "0E8A16", "Crucible real-world validation passed"),
    ("crucible-failed", "B60205", "Crucible real-world validation failed"),
    ("deployed-staging", "1D76DB", "Promoted to staging, health check passed"),
    ("staging-failed", "B60205", "Staging deploy failed"),
    ("needs-live-env", "FBCA04", "Needs live environment for Crucible"),
    ("released", "0E8A16", "Published and released"),
    ("release-failed", "B60205", "Publish step failed"),
    ("done", "0E8A16", "Issue fully complete"),
)

# ── CI workflow template ──────────────────────────────────────────

_CI_WORKFLOW = """\
name: factory-ci
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup
        run: echo "Configure your build steps here"
      - name: Build
        run: echo "Add your build command"
      - name: Test
        run: echo "Add your test command"
"""

# ── Issue template ────────────────────────────────────────────────

_ISSUE_TEMPLATE = """\
---
name: Factory Task
about: Task managed by Dark Factory
title: ''
labels: factory-task, backlog
assignees: ''
---

## Description

<!-- What needs to be done? -->

## Acceptance Criteria

- [ ] Criterion 1
- [ ] Criterion 2

## Notes

<!-- Any additional context -->
"""


# ── Public API ────────────────────────────────────────────────────


def provision_labels(repo: str) -> int:
    """Create all pipeline labels on *repo*. Returns count created."""
    from dark_factory.integrations.shell import gh  # noqa: PLC0415
    from dark_factory.ui.cli_colors import cprint, styled  # noqa: PLC0415

    created = 0
    for name, color, desc in LABELS:
        result = gh(
            ["label", "create", name, "--repo", repo,
             "--color", color, "--description", desc, "--force"],
            timeout=15,
        )
        if result.returncode == 0:
            cprint(f"    {styled('+', 'success')} {name}")
            created += 1
        else:
            # --force updates existing, so failure is unexpected
            cprint(f"    {styled('!', 'warning')} {name}: {result.stderr.strip()}")
    return created


def provision_issue_template(repo: str) -> bool:
    """Create ``.github/ISSUE_TEMPLATE/factory-task.md`` via the API."""
    import base64  # noqa: PLC0415

    from dark_factory.integrations.shell import gh  # noqa: PLC0415

    content = base64.b64encode(_ISSUE_TEMPLATE.encode()).decode()
    path = ".github/ISSUE_TEMPLATE/factory-task.md"
    result = gh(
        ["api", f"repos/{repo}/contents/{path}",
         "-X", "PUT",
         "-f", f"message=chore: add Dark Factory issue template",
         "-f", f"content={content}"],
        timeout=30,
    )
    if result.returncode == 0:
        return True
    # File may already exist — try update with SHA
    if "sha" in result.stderr.lower() or "422" in result.stderr:
        logger.info("Issue template already exists in %s", repo)
        return True
    logger.warning("Failed to create issue template: %s", result.stderr.strip())
    return False


def provision_ci_workflow(repo: str) -> bool:
    """Create ``.github/workflows/factory-ci.yml`` via the API."""
    import base64  # noqa: PLC0415

    from dark_factory.integrations.shell import gh  # noqa: PLC0415

    content = base64.b64encode(_CI_WORKFLOW.encode()).decode()
    path = ".github/workflows/factory-ci.yml"
    result = gh(
        ["api", f"repos/{repo}/contents/{path}",
         "-X", "PUT",
         "-f", f"message=chore: add Dark Factory CI workflow",
         "-f", f"content={content}"],
        timeout=30,
    )
    if result.returncode == 0:
        return True
    if "sha" in result.stderr.lower() or "422" in result.stderr:
        logger.info("CI workflow already exists in %s", repo)
        return True
    logger.warning("Failed to create CI workflow: %s", result.stderr.strip())
    return False


def provision_branch_protection(repo: str, branch: str = "main") -> bool:
    """Set branch protection: require CI, enable auto-merge, no force push."""
    from dark_factory.integrations.shell import gh  # noqa: PLC0415

    result = gh(
        ["api", f"repos/{repo}/branches/{branch}/protection",
         "-X", "PUT",
         "-f", "required_status_checks[strict]=true",
         "-f", "required_status_checks[contexts][]=ci",
         "-f", "enforce_admins=false",
         "-F", "required_pull_request_reviews=null",
         "-F", "restrictions=null",
         "-f", "allow_force_pushes=false",
         "-f", "allow_deletions=false"],
        timeout=30,
    )
    if result.returncode == 0:
        return True
    # Soft failure — branch protection requires admin access and may not be
    # available on free-tier repos.  Not critical for Dark Factory operation.
    logger.info("Branch protection skipped: %s", result.stderr.strip())
    return False


def provision_github(repo: str) -> dict[str, bool | int]:
    """Run all provisioning steps. Returns a status dict."""
    from dark_factory.ui.cli_colors import cprint, print_stage_result, styled  # noqa: PLC0415

    results: dict[str, bool | int] = {}

    cprint(styled("  Pipeline labels", "info"))
    results["labels"] = provision_labels(repo)

    cprint(styled("  Issue template", "info"))
    ok = provision_issue_template(repo)
    print_stage_result("issue template", "passed" if ok else "skipped")
    results["issue_template"] = ok

    cprint(styled("  CI workflow", "info"))
    ok = provision_ci_workflow(repo)
    print_stage_result("CI workflow", "passed" if ok else "skipped")
    results["ci_workflow"] = ok

    cprint(styled("  Branch protection", "info"))
    ok = provision_branch_protection(repo)
    print_stage_result("branch protection", "passed" if ok else "skipped",
                       detail="" if ok else "requires admin access")
    results["branch_protection"] = ok

    return results
