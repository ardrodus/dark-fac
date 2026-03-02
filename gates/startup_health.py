"""Startup health gate — migrated from startup-health-gate.sh (US-034/US-026).

Full diagnostic on factory startup that blocks on critical failures and
warns about degraded non-critical systems.

**Critical** (factory blocked if any fail):
  - GitHub API (``gh`` authenticated and rate limit OK)
  - Claude CLI (``claude`` reachable)

**Non-critical** (factory starts with warnings):
  - Docker daemon
  - claude-mem plugin

**Configuration**:
  - ``config.json`` parseable with required fields
  - Secrets directory exists

Uses :class:`GateRunner` from :mod:`factory.gates.framework`.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from factory.gates.framework import GateReport, GateRunner
from factory.integrations.shell import run_command

logger = logging.getLogger(__name__)


def _cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


# ── Critical checks ──────────────────────────────────────────────


def _check_github_api() -> bool | str:
    if not _cmd_exists("gh"):
        raise RuntimeError("gh CLI not found")
    result = run_command(["gh", "auth", "status"], timeout=15)
    if result.returncode != 0:
        raise RuntimeError("gh not authenticated")
    rate = run_command(["gh", "api", "rate_limit"], timeout=15)
    if rate.returncode != 0:
        raise RuntimeError("GitHub API unreachable")
    return "GitHub API OK (rate response received)"


def _check_claude_cli() -> bool | str:
    if not _cmd_exists("claude"):
        raise RuntimeError("claude CLI not found")
    result = run_command(["claude", "--version"], timeout=15)
    version = result.stdout.strip() or "unknown"
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI check failed ({version})")
    return f"Claude CLI OK ({version})"


# ── Non-critical checks ──────────────────────────────────────────


def _check_docker() -> bool | str:
    if not _cmd_exists("docker"):
        return "docker CLI not found — non-critical, skipped"
    result = run_command(["docker", "info"], timeout=15)
    if result.returncode != 0:
        return "Docker daemon not responding — non-critical"
    ver = run_command(["docker", "version", "--format", "{{.Server.Version}}"], timeout=10)
    version = ver.stdout.strip() or "unknown"
    return f"Docker OK (v{version})"


def _check_claude_mem() -> bool | str:
    if not _cmd_exists("claude"):
        return "claude CLI not available — claude-mem check skipped"
    search_dirs: list[Path] = []
    home = Path.home()
    if (home / ".claude").is_dir():
        search_dirs.append(home / ".claude")
    import os  # noqa: PLC0415
    appdata = os.environ.get("APPDATA", "")
    if appdata and (Path(appdata) / "claude").is_dir():
        search_dirs.append(Path(appdata) / "claude")
    for d in search_dirs:
        plugins = d / "plugins" / "installed_plugins.json"
        if plugins.is_file() and "claude-mem" in plugins.read_text(encoding="utf-8", errors="replace"):
            return "claude-mem plugin installed"
        settings = d / "settings.json"
        if settings.is_file() and "claude-mem" in settings.read_text(encoding="utf-8", errors="replace"):
            return "claude-mem plugin detected in settings"
    return "claude-mem plugin not detected — non-critical"


# ── Configuration checks ─────────────────────────────────────────


def _check_config(config_path: Path) -> bool | str:
    if not config_path.is_file():
        raise RuntimeError(f"config.json not found at {config_path}")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"config.json is not valid JSON: {exc}") from exc
    version = data.get("version")
    if not version:
        raise RuntimeError("config.json missing 'version' field")
    repos = data.get("repos", [])
    return f"config OK (v{version}, {len(repos)} repo(s))"


def _check_secrets(secrets_dir: Path) -> bool | str:
    if not secrets_dir.is_dir():
        return "secrets directory not found — will be created on first use"
    return f"secrets directory exists at {secrets_dir}"


# ── Discovery & public API ────────────────────────────────────────

GATE_NAME = "startup-health"


def _make_runner(
    cfg: Path, sec: Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    runner = GateRunner(GATE_NAME, metrics_dir=metrics_dir)
    runner.register_check("github-api", _check_github_api, timeout=30.0)
    runner.register_check("claude-cli", _check_claude_cli, timeout=30.0)
    runner.register_check("docker", _check_docker, timeout=30.0)
    runner.register_check("claude-mem", _check_claude_mem)
    runner.register_check("config-json", lambda: _check_config(cfg))
    runner.register_check("secrets-dir", lambda: _check_secrets(sec))
    return runner


def create_runner(
    workspace: str | Path, *, metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Create a configured (but not executed) startup-health gate runner."""
    root = Path(workspace)
    return _make_runner(
        root / ".dark-factory" / "config.json",
        root / ".dark-factory" / ".secrets",
        metrics_dir=metrics_dir,
    )


def run_startup_health(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
    secrets_dir: str | Path | None = None,
    metrics_dir: str | Path | None = None,
) -> GateReport:
    """Run the startup health gate."""
    root = Path(repo_root)
    return _make_runner(
        Path(config_path) if config_path else root / ".dark-factory" / "config.json",
        Path(secrets_dir) if secrets_dir else root / ".dark-factory" / ".secrets",
        metrics_dir=metrics_dir,
    ).run()
