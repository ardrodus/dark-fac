"""GitHub authentication flows.

Ports the four auth methods (CLI, PAT, SSH, App) and the connect/auto-connect orchestrators.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from dark_factory.core.config_manager import (
    get_config_value,
    load_config,
    resolve_config_dir,
    save_config,
    set_config_value,
)

logger = logging.getLogger(__name__)

_PROMPT_SUPPRESS: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "", "SSH_ASKPASS": "", "GCM_CREDENTIAL_STORE": "cache",
}


def suppress_prompts() -> None:
    """Inject prompt-suppression env vars into the current process."""
    for k, v in _PROMPT_SUPPRESS.items():
        os.environ.setdefault(k, v)


def auth_github_cli() -> bool:
    """Validate ``gh auth status`` and cache *GH_TOKEN*."""
    from dark_factory.integrations.shell import gh, run_command  # noqa: PLC0415

    result = gh(["auth", "status"], timeout=15)
    if result.returncode == 0:
        logger.info("Already authenticated via GitHub CLI")
        tok = gh(["auth", "token"], timeout=10)
        if tok.returncode == 0 and tok.stdout.strip():
            os.environ["GH_TOKEN"] = tok.stdout.strip()
        _save_auth_method("gh-cli")
        return True
    if not sys.stdin.isatty():
        logger.warning("gh CLI not authenticated and stdin is not a TTY")
        return False
    print("\n  GitHub CLI Authentication\n\n  GitHub CLI not authenticated.")
    try:
        input("  Press Enter to log in via gh auth login...")
    except (EOFError, KeyboardInterrupt):
        return False
    if run_command(["gh", "auth", "login"], timeout=120).returncode == 0:
        logger.info("GitHub CLI authentication successful")
        _save_auth_method("gh-cli")
        return True
    logger.error("GitHub CLI authentication failed")
    return False


def auth_github_pat() -> bool:
    """Prompt for and validate a personal access token."""
    if not sys.stdin.isatty():
        return False
    print("\n  Personal Access Token Authentication\n")
    print("  Create a token at: https://github.com/settings/tokens")
    print("  Required scopes: repo, read:org, workflow\n")
    try:
        token = getpass.getpass("  Paste your token (hidden): ")
    except (EOFError, KeyboardInterrupt):
        return False
    if not token:
        print("  No token provided")
        return False
    proc = subprocess.run(  # noqa: S603
        ["gh", "auth", "login", "--with-token"],
        input=token, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        print("  Token authentication failed -- check token scopes")
        return False
    print("  Token accepted -- authenticated via PAT")
    os.environ["GH_TOKEN"] = token
    _write_secret("github-pat", token)
    _save_auth_method("pat")
    return True


def auth_github_ssh() -> bool:
    """Validate SSH key access to ``github.com``."""
    from dark_factory.integrations.shell import run_command  # noqa: PLC0415

    result = run_command(
        ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new", "git@github.com"],
        timeout=15,
    )
    combined = f"{result.stdout} {result.stderr}".lower()
    if "successfully authenticated" in combined:
        logger.info("SSH key verified")
        _save_auth_method("ssh")
        return True
    logger.warning("SSH test output: %s", combined.strip())
    if not sys.stdin.isatty():
        return False
    print(f"  SSH test returned: {combined.strip()}")
    print("  Make sure your SSH key is added to https://github.com/settings/keys")
    try:
        if input("  Continue anyway? (y/n): ").strip().lower() != "y":
            return False
    except (EOFError, KeyboardInterrupt):
        return False
    _save_auth_method("ssh")
    return True


def auth_github_app() -> bool:
    """Configure GitHub App installation auth (placeholder)."""
    from dark_factory.integrations.shell import gh, run_command  # noqa: PLC0415

    if not sys.stdin.isatty():
        return False
    print("\n  GitHub App Authentication\n\n  GitHub App auth is not yet fully implemented.\n")
    try:
        app_id = input("  App ID: ").strip()
        install_id = input("  Installation ID: ").strip()
        key_path = input("  Private key path (.pem): ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if not app_id or not install_id or not key_path:
        print("  All fields are required")
        return False
    if not Path(key_path).is_file():
        print(f"  Private key file not found: {key_path}")
        return False
    _write_secret("github-app.json", json.dumps(
        {"app_id": app_id, "installation_id": install_id, "private_key_path": key_path},
        indent=2,
    ))
    if gh(["auth", "status"], timeout=15).returncode != 0:
        run_command(["gh", "auth", "login"], timeout=120)
    _save_auth_method("github-app")
    return True


_AUTH_METHODS: list[tuple[str, str, object]] = [
    ("1", "GitHub CLI (recommended)", auth_github_cli),
    ("2", "Personal Access Token", auth_github_pat),
    ("3", "SSH Key", auth_github_ssh),
    ("4", "GitHub App", auth_github_app),
]


def connect_github() -> bool:
    """Interactive auth method selection. Returns ``True`` on success."""
    suppress_prompts()
    print("\n  GitHub Authentication\n")
    for key, label, _ in _AUTH_METHODS:
        print(f"  [{key}] {label}")
    print()
    try:
        choice = input("  Select auth method [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        return False
    for key, _, handler in _AUTH_METHODS:
        if choice == key:
            return handler()  # type: ignore[operator]
    print("  Invalid selection.")
    return False


def auto_connect_github() -> bool:
    """Use cached credentials without prompting."""
    from dark_factory.integrations.shell import gh  # noqa: PLC0415

    suppress_prompts()
    if gh(["auth", "status"], timeout=15).returncode != 0:
        logger.error("GitHub CLI not authenticated. Run: gh auth login")
        return False
    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        repos = get_config_value(load_config(), "repos")
        if isinstance(repos, list):
            for r in repos:
                if isinstance(r, dict) and r.get("active"):
                    repo = r.get("name", "")  # type: ignore[assignment]
                    break
    if not repo:
        logger.error("No repo configured. Set GITHUB_REPO or run connect_github().")
        return False
    if gh(["repo", "view", repo], timeout=30).returncode != 0:
        logger.error("Cannot access repo: %s", repo)
        return False
    os.environ["GITHUB_REPO"] = repo
    logger.info("[AUTO] GitHub: %s", repo)
    return True


def _save_auth_method(method: str) -> None:
    cfg = load_config()
    set_config_value(cfg, "auth_method", method)
    save_config(cfg)


def _write_secret(name: str, content: str) -> None:
    dest = resolve_config_dir() / ".secrets"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / name).write_text(content, encoding="utf-8")
