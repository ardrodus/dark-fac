"""GitHub repo provisioning: labels, workflows, secrets, branch protection."""
from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from factory.integrations.shell import gh

logger = logging.getLogger(__name__)

_LABELS: tuple[tuple[str, str, str], ...] = (
    ("factory-task", "0E8A16", "Dark Factory managed task"),
    ("backlog", "C2E0C6", "New, ready for architecture review"),
    ("queued", "C2E0C6", "Queued for pipeline processing"),
    ("arch-review", "FBCA04", "Architecture pipeline is reviewing"),
    ("arch-approved", "0075CA", "Pipeline approved, ready for engineer"),
    ("in-progress", "D93F0B", "Assigned to a TDD pipeline"),
    ("blocked", "B60205", "Blocked by dependency or failure"),
    ("in-review", "BFD4F2", "PR open, CI running"),
    ("completed", "0E8A16", "Merged to main"),
    ("human-review", "B60205", "Agent failed, needs human"),
    ("deployed-dev", "1D76DB", "Deployed to dev, health check passed"),
    ("deploy-failed", "B60205", "Deploy to dev failed"),
    ("crucible-passed", "0E8A16", "Crucible real-world validation passed"),
    ("crucible-failed", "B60205", "Crucible real-world validation failed"),
    ("deployed-staging", "1D76DB", "Promoted to staging, health check passed"),
    ("staging-failed", "B60205", "Staging deploy failed"),
    ("needs-live-env", "FBCA04", "Needs live environment for Crucible validation"),
    ("released", "0E8A16", "Published and released"),
    ("release-failed", "B60205", "Publish step failed"),
    ("done", "0E8A16", "Issue fully complete"),
)

_CI_WORKFLOW = """\
name: Factory CI
on:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build container
        run: docker compose build
      - name: Start services
        run: docker compose up -d
      - name: Wait for services
        run: sleep 5 && docker compose ps
      - name: Build
        run: docker compose exec -T app sh -c "${BUILD_CMD:-echo 'no build configured'}"
        env:
          BUILD_CMD: ${{ vars.BUILD_CMD }}
      - name: Test
        run: docker compose exec -T app sh -c "${TEST_CMD:-echo 'no tests configured'}"
        env:
          TEST_CMD: ${{ vars.TEST_CMD }}
      - name: Stop services
        if: always()
        run: docker compose down
  auto-merge:
    runs-on: ubuntu-latest
    needs: test
    if: success()
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: actions/github-script@v7
        with:
          script: |
            const pr = context.payload.pull_request;
            if (!pr.head.ref.startsWith('factory/')) return;
            await github.rest.pulls.merge({
              owner: context.repo.owner, repo: context.repo.repo,
              pull_number: pr.number, merge_method: 'squash',
            });
            const m = pr.body.match(/Closes #(\\d+)/);
            if (m) {
              const n = parseInt(m[1]);
              await github.rest.issues.update({
                owner: context.repo.owner, repo: context.repo.repo,
                issue_number: n, state: 'closed',
              });
              await github.rest.issues.addLabels({
                owner: context.repo.owner, repo: context.repo.repo,
                issue_number: n, labels: ['completed'],
              });
            }
"""


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    """Summary of provisioning actions taken."""
    labels_created: int
    workflows_installed: tuple[str, ...]
    secrets_set: tuple[str, ...]
    branch_protection: bool


def _owner_name(repo: str) -> tuple[str, str]:
    parts = repo.split("/")
    if len(parts) != 2:  # noqa: PLR2004
        raise ValueError(f"Expected owner/repo, got: {repo}")
    return parts[0], parts[1]


def provision_labels(repo: str) -> int:
    """Create missing factory labels. Returns count created."""
    existing: set[str] = set()
    r = gh(["label", "list", "--repo", repo, "--json", "name", "--limit", "200"])
    if r.returncode == 0 and r.stdout.strip():
        try:
            existing = {it["name"] for it in json.loads(r.stdout)
                        if isinstance(it, dict) and isinstance(it.get("name"), str)}
        except json.JSONDecodeError:
            pass
    created = 0
    for nm, color, desc in _LABELS:
        if nm in existing:
            continue
        if gh(["label", "create", nm, "--repo", repo,
               "--color", color, "--description", desc]).returncode == 0:
            created += 1
        else:
            logger.warning("Failed to create label: %s", nm)
    return created


def provision_workflows(repo_root: Path) -> tuple[str, ...]:
    """Install CI/CD workflow files. Returns names written."""
    wf_dir = repo_root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    ci = wf_dir / "factory-ci.yml"
    if ci.exists():
        return ()
    ci.write_text(_CI_WORKFLOW, encoding="utf-8")
    return ("factory-ci.yml",)


def provision_secrets(repo: str) -> tuple[str, ...]:
    """Prompt for repo secrets. Returns names configured."""
    if not sys.stdin.isatty():
        return ()
    prompts = [("AWS_DEPLOY_ROLE_ARN", "AWS deploy role ARN (or Enter to skip)")]
    configured: list[str] = []
    for nm, txt in prompts:
        try:
            val = getpass.getpass(f"  {txt}: ")
        except (EOFError, KeyboardInterrupt):
            break
        if not val.strip():
            continue
        if gh(["secret", "set", nm, "--repo", repo,
               "--body", val.strip()]).returncode == 0:
            configured.append(nm)
        else:
            logger.warning("Failed to set secret: %s", nm)
    return tuple(configured)


def provision_branch_protection(repo: str) -> bool:
    """Set branch protection on main. Returns True if applied."""
    owner, name = _owner_name(repo)
    ep = f"repos/{owner}/{name}/branches/main/protection"
    chk = gh(["api", ep], timeout=30)
    if chk.returncode == 0 and chk.stdout.strip() not in ("", "none"):
        return False
    payload = json.dumps({"required_status_checks": {"strict": True, "contexts": ["test"]},
        "enforce_admins": False, "required_pull_request_reviews": None,
        "restrictions": None, "allow_auto_merge": True,
        "allow_force_pushes": False, "allow_deletions": False})
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", "--method", "PUT", ep, "--input", "-"],
        input=payload, capture_output=True, text=True, timeout=30,
        env={**os.environ},
    )
    if proc.returncode != 0:
        logger.warning("Failed to set branch protection: %s", proc.stderr.strip())
        return False
    return True


def provision_github(repo: str, repo_root: Path | None = None) -> ProvisionResult:
    """Run all GitHub provisioning steps. Main entry point."""
    _owner_name(repo)  # validate format
    root = repo_root or Path.cwd()
    return ProvisionResult(
        labels_created=provision_labels(repo),
        workflows_installed=provision_workflows(root),
        secrets_set=provision_secrets(repo),
        branch_protection=provision_branch_protection(repo),
    )
