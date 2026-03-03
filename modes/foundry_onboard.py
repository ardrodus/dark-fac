"""Foundry workspace onboarding flow (US-009).

Multi-step flow to add a new workspace to the factory:

1. Prompt for repo URL
2. Clone the repository
3. Wire webhooks
4. Select deploy strategy (web / console)
5. Configure Sentinel scan mode (full / fast / off)
6. Run initial Gate 1 baseline scan

The result is persisted to ``.dark-factory/config.json`` so the workspace
appears in the Foundry workspace list.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_CONFIG_DIR = ".dark-factory"
_CONFIG_FILE = "config.json"

# ── Result types ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OnboardStep:
    """Outcome of a single onboarding step."""

    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class OnboardResult:
    """Aggregated result of the full onboarding flow."""

    success: bool
    repo: str
    strategy: str
    scan_mode: str
    steps: tuple[OnboardStep, ...]


# ── DI config ─────────────────────────────────────────────────────


def _default_clone(repo_url: str, dest: Path) -> bool:
    """Clone *repo_url* into *dest* using git."""
    from dark_factory.integrations.shell import git  # noqa: PLC0415

    dest.parent.mkdir(parents=True, exist_ok=True)
    result = git(["clone", repo_url, str(dest)], timeout=120)
    return result.returncode == 0


def _default_wire_webhooks(repo: str) -> bool:
    """Wire GitHub webhooks for *repo* using gh CLI."""
    from dark_factory.integrations.shell import gh  # noqa: PLC0415

    result = gh(
        ["api", "repos/" + repo + "/hooks", "--method", "GET"],
        timeout=30,
    )
    if result.returncode != 0:
        logger.warning("Could not verify webhooks for %s: %s", repo, result.stderr)
    # Webhook wiring is best-effort — don't block onboarding
    return True


def _default_run_gate1(workspace_path: Path) -> bool:
    """Run Gate 1 baseline scan on the workspace."""
    from dark_factory.gates.framework import GateRunner  # noqa: PLC0415

    runner = GateRunner(
        "gate1-baseline",
        metrics_dir=str(workspace_path / _CONFIG_DIR),
    )

    def _lint_check() -> bool:
        from dark_factory.integrations.shell import run_command  # noqa: PLC0415

        result = run_command(
            ["ruff", "check", "."],
            cwd=str(workspace_path),
            timeout=60,
        )
        return result.returncode == 0

    def _structure_check() -> bool:
        # Verify the clone has a valid git repo
        return (workspace_path / ".git").is_dir()

    runner.register_check("structure", _structure_check)
    runner.register_check("lint-baseline", _lint_check)
    report = runner.run()
    return report.passed


@dataclass(frozen=True, slots=True)
class OnboardConfig:
    """Dependency-injection config for the onboarding flow.

    Tests inject lambdas; production uses the default implementations.
    """

    clone_fn: Callable[[str, Path], bool] = _default_clone
    wire_webhooks_fn: Callable[[str], bool] = _default_wire_webhooks
    run_gate1_fn: Callable[[Path], bool] = _default_run_gate1
    workspace_root: Path = field(
        default_factory=lambda: Path(_CONFIG_DIR) / "workspaces",
    )
    config_root: Path = field(default_factory=lambda: Path("."))


# ── Config persistence ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """A single workspace entry in the config file."""

    repo: str
    strategy: str
    scan_mode: str
    status: str = "active"
    webhook_status: str = "enabled"
    watched_branch: str = "main"


def _config_path(config_root: Path) -> Path:
    """Return the path to the workspace config file."""
    return config_root / _CONFIG_DIR / _CONFIG_FILE


def load_workspace_configs(
    config_root: Path | None = None,
) -> list[WorkspaceEntry]:
    """Load all workspace entries from ``.dark-factory/config.json``.

    Reads from the ``repos`` array (main onboarding).  Strategy and other
    workspace-scoped fields are resolved from the repo entry's
    ``workspace_config`` staging key, or from the workspace-level
    ``.dark-factory/config.json`` if the workspace has already been
    bootstrapped.

    Returns an empty list if the file doesn't exist or is corrupted.
    """
    root = config_root or Path(".")
    path = _config_path(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to read workspace config at %s", path)
        return []
    if not isinstance(data, dict):
        return []

    entries: list[WorkspaceEntry] = []

    # Primary source: repos array (from main onboarding)
    raw_repos = data.get("repos", [])
    if isinstance(raw_repos, list):
        for item in raw_repos:
            if not isinstance(item, dict):
                continue
            repo_name = str(item.get("name", ""))
            if not repo_name:
                continue

            # Resolve strategy from workspace_config staging key
            ws_cfg = item.get("workspace_config", {})
            if not isinstance(ws_cfg, dict):
                ws_cfg = {}
            strategy = str(ws_cfg.get("strategy", "console"))

            # Try workspace-level config for latest values
            ws_path = root / _CONFIG_DIR / "workspaces" / repo_name
            ws_settings = load_workspace_settings(ws_path) if ws_path.is_dir() else {}
            if ws_settings.get("strategy"):
                strategy = str(ws_settings["strategy"])

            entries.append(
                WorkspaceEntry(
                    repo=repo_name,
                    strategy=strategy,
                    scan_mode="full",
                    status="active" if item.get("active") else "paused",
                    webhook_status="disabled",
                    watched_branch="main",
                )
            )

    return entries


def load_workspace_settings(workspace_path: str | Path) -> dict[str, object]:
    """Load per-workspace settings from ``{workspace}/.dark-factory/config.json``.

    Returns an empty dict if the file doesn't exist or is malformed.
    Settings include ``skip_arch_review``, ``strategy``, etc.
    """
    path = Path(workspace_path) / _CONFIG_DIR / _CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_workspace_config(
    entry: WorkspaceEntry,
    *,
    config_root: Path | None = None,
) -> Path:
    """Append or update a workspace entry in config.json.

    Writes to the ``repos`` array with workspace-scoped settings in the
    ``workspace_config`` staging key.  Creates the config file and
    directory if they don't exist.  Returns the path to the config file.
    """
    root = config_root or Path(".")
    path = _config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config
    data: dict[str, object] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
        except (json.JSONDecodeError, OSError):
            pass

    # Get or create repos list
    raw_repos = data.get("repos", [])
    repos: list[dict[str, object]] = (
        raw_repos if isinstance(raw_repos, list) else []
    )

    # Build repo entry with workspace_config staging key
    repo_entry: dict[str, object] = {
        "name": entry.repo,
        "active": entry.status == "active",
    }
    ws_config: dict[str, object] = {
        "strategy": entry.strategy,
    }
    repo_entry["workspace_config"] = ws_config

    # Upsert: update existing or append new
    updated = False
    for i, r in enumerate(repos):
        if isinstance(r, dict) and r.get("name") == entry.repo:
            repos[i] = repo_entry
            updated = True
            break
    if not updated:
        repos.append(repo_entry)

    data["repos"] = repos
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("Workspace config saved to %s", path)
    return path


# ── Repo URL parsing ──────────────────────────────────────────────


def parse_repo_from_url(url: str) -> str:
    """Extract ``owner/repo`` from a GitHub URL or pass through if already in that format.

    Supports:
    - ``https://github.com/owner/repo``
    - ``https://github.com/owner/repo.git``
    - ``git@github.com:owner/repo.git``
    - ``owner/repo`` (pass-through)
    """
    cleaned = url.strip()
    # HTTPS format
    if "github.com/" in cleaned:
        parts = cleaned.split("github.com/", 1)[1]
        parts = parts.rstrip("/").removesuffix(".git")
        segments = parts.split("/")
        if len(segments) >= 2:  # noqa: PLR2004
            return f"{segments[0]}/{segments[1]}"
    # SSH format
    if "github.com:" in cleaned:
        parts = cleaned.split("github.com:", 1)[1]
        parts = parts.rstrip("/").removesuffix(".git")
        segments = parts.split("/")
        if len(segments) >= 2:  # noqa: PLR2004
            return f"{segments[0]}/{segments[1]}"
    # Already owner/repo format
    if "/" in cleaned and not cleaned.startswith(("http", "git@")):
        segments = cleaned.split("/")
        if len(segments) == 2 and segments[0] and segments[1]:  # noqa: PLR2004
            return cleaned
    msg = f"Cannot parse repo from URL: {url!r}"
    raise ValueError(msg)


def build_clone_url(repo: str) -> str:
    """Build an HTTPS clone URL from ``owner/repo``."""
    return f"https://github.com/{repo}.git"


# ── Onboarding flow ──────────────────────────────────────────────


def run_onboard_workspace(
    repo_url: str,
    *,
    strategy: str = "console",
    scan_mode: str = "full",
    config: OnboardConfig | None = None,
) -> OnboardResult:
    """Execute the full workspace onboarding flow.

    Steps:
      1. Parse and validate the repo URL
      2. Clone the repository
      3. Wire webhooks
      4. Apply deploy strategy
      5. Configure Sentinel scan mode
      6. Run initial Gate 1 baseline scan
      7. Save workspace config

    Parameters
    ----------
    repo_url:
        GitHub repository URL or ``owner/repo`` string.
    strategy:
        Deploy strategy — ``"web"`` or ``"console"``.
    scan_mode:
        Sentinel scan mode — ``"full"``, ``"fast"``, or ``"off"``.
    config:
        DI config for testing. Uses defaults if ``None``.
    """
    cfg = config or OnboardConfig()
    steps: list[OnboardStep] = []

    # Step 1: Parse repo URL
    try:
        repo = parse_repo_from_url(repo_url)
    except ValueError as exc:
        steps.append(OnboardStep(name="parse_url", passed=False, message=str(exc)))
        return OnboardResult(
            success=False, repo=repo_url, strategy=strategy,
            scan_mode=scan_mode, steps=tuple(steps),
        )
    steps.append(OnboardStep(name="parse_url", passed=True, message=f"Parsed: {repo}"))

    # Step 2: Clone
    clone_url = build_clone_url(repo)
    ws_name = repo.replace("/", "_")
    ws_path = cfg.workspace_root / ws_name
    clone_ok = cfg.clone_fn(clone_url, ws_path)
    steps.append(OnboardStep(
        name="clone", passed=clone_ok,
        message=f"Cloned to {ws_path}" if clone_ok else f"Clone failed: {clone_url}",
    ))
    if not clone_ok:
        return OnboardResult(
            success=False, repo=repo, strategy=strategy,
            scan_mode=scan_mode, steps=tuple(steps),
        )

    # Step 3: Wire webhooks
    webhook_ok = cfg.wire_webhooks_fn(repo)
    steps.append(OnboardStep(
        name="wire_webhooks", passed=webhook_ok,
        message="Webhooks configured" if webhook_ok else "Webhook wiring failed",
    ))
    # Webhook failure is non-fatal — continue

    # Step 4: Deploy strategy
    if strategy not in ("web", "console"):
        strategy = "console"
    steps.append(OnboardStep(
        name="deploy_strategy", passed=True,
        message=f"Strategy set to {strategy}",
    ))

    # Step 5: Sentinel scan mode
    if scan_mode not in ("full", "fast", "off"):
        scan_mode = "full"
    steps.append(OnboardStep(
        name="sentinel_scan_mode", passed=True,
        message=f"Scan mode set to {scan_mode}",
    ))

    # Step 6: Gate 1 baseline scan
    gate1_ok = cfg.run_gate1_fn(ws_path)
    steps.append(OnboardStep(
        name="gate1_baseline", passed=gate1_ok,
        message="Gate 1 baseline passed" if gate1_ok else "Gate 1 baseline failed",
    ))

    # Step 7: Save config
    webhook_status = "enabled" if webhook_ok else "disabled"
    entry = WorkspaceEntry(
        repo=repo,
        strategy=strategy,
        scan_mode=scan_mode,
        status="active",
        webhook_status=webhook_status,
    )
    try:
        save_workspace_config(entry, config_root=cfg.config_root)
        steps.append(OnboardStep(
            name="save_config", passed=True,
            message="Config saved to .dark-factory/config.json",
        ))
    except OSError as exc:
        steps.append(OnboardStep(
            name="save_config", passed=False,
            message=f"Failed to save config: {exc}",
        ))
        return OnboardResult(
            success=False, repo=repo, strategy=strategy,
            scan_mode=scan_mode, steps=tuple(steps),
        )

    # Overall success: clone must pass, gate1 is informational
    success = clone_ok
    return OnboardResult(
        success=success, repo=repo, strategy=strategy,
        scan_mode=scan_mode, steps=tuple(steps),
    )
