"""Crucible repo provisioning — create/manage the ``{repo}-crucible`` companion."""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dark_factory.core.config_manager import ConfigData
from dark_factory.integrations.shell import gh, git

logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class CrucibleRepoResult:
    """Outcome of a Crucible repo provisioning or management operation."""
    crucible_repo: str
    created: bool
    cloned: bool
    local_path: str
    error: str = ""

def _split_repo(repo: str) -> tuple[str, str]:
    parts = repo.split("/")
    if len(parts) != 2:  # noqa: PLR2004
        raise ValueError(f"Expected owner/repo, got: {repo!r}")
    return parts[0], parts[1]

def _clone_url(repo: str) -> str:
    token = os.environ.get("GH_TOKEN", "")
    return f"https://x-access-token:{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"

def _repo_exists(cr: str) -> bool:
    return gh(["repo", "view", cr], timeout=30).returncode == 0

def _scaffold(target: Path, repo_name: str) -> None:
    for d in ("tests", "reports", "screenshots", "helpers", "fixtures"):
        (target / d).mkdir(parents=True, exist_ok=True)
    (target / "playwright.config.ts").write_text(
        "import { defineConfig } from '@playwright/test';\n\nexport default defineConfig({\n"
        "  testDir: './tests',\n  timeout: 30_000,\n  retries: 1,\n  use: {\n"
        "    baseURL: process.env.DEV_ENDPOINT,\n    screenshot: 'only-on-failure',\n"
        "    trace: 'retain-on-failure',\n  },\n  reporter: [\n"
        "    ['html', { outputFolder: './reports' }],\n"
        "    ['json', { outputFile: './reports/results.json' }],\n  ],\n});\n", encoding="utf-8")
    pkg = {"name": "crucible-tests", "private": True,
           "description": f"Crucible real-world validation tests for {repo_name}",
           "scripts": {"test": "npx playwright test", "test:ui": "npx playwright test --ui"},
           "devDependencies": {"@playwright/test": "^1.40.0"}}
    (target / "package.json").write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    (target / ".gitignore").write_text(
        "node_modules/\ntest-results/\nplaywright-report/\nreports/\nscreenshots/\n.env\n", encoding="utf-8")
    (target / "README.md").write_text(
        "# Crucible Tests\n\nReal-world validation tests managed by the Dark Factory "
        "Crucible agent.\n\n**Do not manually edit tests in this repo.** The Crucible agent "
        "writes, runs, and\ncommits tests automatically after each PR deploys to dev.\n", encoding="utf-8")

def _init_and_push(init_dir: Path, cr: str) -> bool:
    cwd = str(init_dir)
    for args in (["init"], ["add", "-A"], ["commit", "-m", "Initialize Crucible test repo"],
                 ["branch", "-M", "main"], ["remote", "add", "origin", _clone_url(cr)]):
        git(args, cwd=cwd)
    r = git(["push", "-u", "origin", "main"], cwd=cwd)
    if r.returncode != 0:
        logger.warning("Push failed for %s: %s", cr, r.stderr.strip())
    return r.returncode == 0

def _create_repo(cr: str, repo_name: str) -> str:
    r = gh(["repo", "create", cr, "--private",
            "--description", f"Crucible tests for {repo_name} -- managed by Dark Factory"], timeout=30)
    return "" if r.returncode == 0 else f"gh repo create failed: {r.stderr.strip()}"

def _res(cr: str, **kw: object) -> CrucibleRepoResult:
    return CrucibleRepoResult(crucible_repo=cr, created=bool(kw.get("created", False)),
                              cloned=bool(kw.get("cloned", False)),
                              local_path=str(kw.get("local_path", "")),
                              error=str(kw.get("error", "")))


# ── Public API ──────────────────────────────────────────────────


def provision_crucible_repo(repo: str, config: ConfigData) -> CrucibleRepoResult:  # noqa: ARG001
    """Create the ``{repo}-crucible`` companion repo if needed. Idempotent."""
    owner, name = _split_repo(repo)
    cr = f"{owner}/{name}-crucible"
    if _repo_exists(cr):
        logger.info("Crucible repo already exists: %s", cr)
        return _res(cr)
    err = _create_repo(cr, name)
    if err:
        return _res(cr, error=err)
    init_dir = Path(tempfile.mkdtemp(prefix="crucible-init-"))
    try:
        _scaffold(init_dir, name)
        pushed = _init_and_push(init_dir, cr)
    finally:
        shutil.rmtree(init_dir, ignore_errors=True)
    if not pushed:
        return _res(cr, created=True, error="Repo created but initial push failed")
    logger.info("Created and initialized: %s", cr)
    return _res(cr, created=True)

def manage_crucible_repo(repo: str, target_dir: Path | None = None) -> CrucibleRepoResult:
    """Sync the Crucible test repo locally: pull, clone, or create + scaffold."""
    owner, name = _split_repo(repo)
    cr = f"{owner}/{name}-crucible"
    local = target_dir or (Path.cwd() / ".dark-factory" / "crucible")
    # Case 1: local clone exists — fast-forward pull
    if (local / ".git").is_dir():
        r = git(["pull", "--ff-only"], cwd=str(local))
        return _res(cr, local_path=str(local),
                    error="" if r.returncode == 0 else "pull failed (diverged?)")
    # Case 2: exists remotely — clone
    if _repo_exists(cr):
        local.parent.mkdir(parents=True, exist_ok=True)
        git(["clone", _clone_url(cr), str(local)])
        ok = (local / ".git").is_dir()
        return _res(cr, cloned=ok, local_path=str(local) if ok else "",
                    error="" if ok else "clone failed")
    # Case 3: create, scaffold, push, then clone
    err = _create_repo(cr, name)
    if err:
        return _res(cr, error=err)
    init_dir = Path(tempfile.mkdtemp(prefix="crucible-init-"))
    try:
        _scaffold(init_dir, name)
        _init_and_push(init_dir, cr)
    finally:
        shutil.rmtree(init_dir, ignore_errors=True)
    local.parent.mkdir(parents=True, exist_ok=True)
    git(["clone", _clone_url(cr), str(local)])
    ok = (local / ".git").is_dir()
    return _res(cr, created=True, cloned=ok, local_path=str(local) if ok else "")
