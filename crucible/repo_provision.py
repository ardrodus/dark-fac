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

def _scaffold(target: Path, repo_name: str, frameworks: list[str] | None = None) -> None:  # noqa: ARG001
    """Scaffold crucible repo with scenario-based layout.

    Scenarios are natural language prompts organized by feature.
    The Crucible agent traverses all subdirectories to find *.scenario files.

    Layout::

        scenarios/
            _example/
                api-health.scenario
            README.md
        crucible.json
        .gitignore
        README.md
    """
    scenarios = target / "scenarios"
    scenarios.mkdir(parents=True, exist_ok=True)

    # Example feature directory with a starter scenario
    example_dir = scenarios / "_example"
    example_dir.mkdir(parents=True, exist_ok=True)
    (example_dir / "api-health.scenario").write_text(
        "# API Health Check\n"
        "#\n"
        "# Verifies the application starts and responds to a basic health probe.\n"
        "#\n"
        "# For web apps: GET the root URL or /health endpoint. Expect HTTP 200.\n"
        "# For console apps: Run the binary with --version or --help. Expect exit code 0.\n"
        "#\n"
        "# This is an example scenario. Replace it with real scenarios for your app.\n\n"
        "If this is a web app:\n"
        "  GET http://localhost:$PORT/health\n"
        "  Expect: HTTP 200 with a JSON body\n\n"
        "If this is a console app:\n"
        "  Run the main binary with --help\n"
        "  Expect: exit code 0 and usage text on stdout\n",
        encoding="utf-8",
    )

    (scenarios / "README.md").write_text(
        "# Scenarios\n\n"
        "Each `.scenario` file is a natural language prompt that the Crucible agent\n"
        "reads and executes using bash/curl. Organize scenarios into feature\n"
        "subdirectories as you see fit.\n\n"
        "## Layout\n\n"
        "```\n"
        "scenarios/\n"
        "  auth/\n"
        "    login.scenario\n"
        "    logout.scenario\n"
        "  cart/\n"
        "    add-item.scenario\n"
        "    checkout.scenario\n"
        "```\n\n"
        "## Naming\n\n"
        "- Graduated (permanent) scenarios: `<name>.scenario`\n"
        "- New PR scenarios (pending graduation): `pr-<number>-<name>.scenario`\n"
        "- After graduation, the `pr-<number>-` prefix is stripped.\n\n"
        "## Writing Scenarios\n\n"
        "Scenarios are plain English instructions, NOT code. Example:\n\n"
        "```\n"
        "# Create User\n"
        "POST /api/users with body {\"name\": \"test\", \"email\": \"test@example.com\"}\n"
        "Expect: HTTP 201 with an 'id' field in the JSON response\n"
        "```\n\n"
        "The Crucible agent will read the instructions and execute them using\n"
        "bash commands (curl, httpx, the app binary, etc.).\n",
        encoding="utf-8",
    )

    (target / "crucible.json").write_text(json.dumps({
        "name": f"{repo_name}-crucible",
        "version": "2.0",
        "type": "scenario",
        "created_by": "dark-factory",
    }, indent=2) + "\n", encoding="utf-8")

    (target / ".gitignore").write_text(
        "reports/\n.env\n__pycache__/\n", encoding="utf-8")

    (target / "README.md").write_text(
        "# Crucible Tests\n\n"
        "Scenario-based validation tests managed by the Dark Factory Crucible agent.\n\n"
        "## How it works\n\n"
        "Scenarios are **natural language prompts** — not code. The Crucible agent reads\n"
        "each `.scenario` file, executes the instructions using bash/curl, and reports\n"
        "PASS or FAIL.\n\n"
        "No test frameworks. No Playwright. No pytest. The LLM + bash + curl IS the\n"
        "test harness.\n\n"
        "## Structure\n\n"
        "```\n"
        "scenarios/           ← organize by feature\n"
        "  auth/\n"
        "    login.scenario\n"
        "  cart/\n"
        "    add-item.scenario\n"
        "crucible.json        ← repo metadata\n"
        "```\n\n"
        "See `scenarios/README.md` for writing guidelines.\n\n"
        "## Graduation\n\n"
        "When a PR passes Crucible validation, its new scenarios are \"graduated\"\n"
        "into this repo permanently — the bill becomes law.\n",
        encoding="utf-8",
    )

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
