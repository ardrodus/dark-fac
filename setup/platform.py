"""Platform detection and dependency checking.

Ports ``detect_platform()`` and dependency-check logic from ``dark-factory.sh``.
"""
from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Platform:
    """Detected host platform information."""
    os: str            # "linux", "macos", "windows", or "unknown"
    arch: str          # "amd64", "arm64", or raw value
    shell: str         # "bash", "powershell", "cmd", "zsh", etc.
    is_wsl: bool       # True inside WSL
    is_git_bash: bool  # True in Git Bash / MSYS2


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """Status of a single required dependency."""
    name: str
    found: bool
    version: str        # version string or ""
    path: str           # resolved binary path or ""
    install_hint: str   # platform-specific install instruction or ""


def _detect_os() -> str:
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if os.environ.get("MSYSTEM", "").upper() or os.environ.get("CYGWIN"):
        return "windows"
    return "unknown"


def _detect_arch() -> str:
    raw = platform.machine().lower()
    if raw in ("x86_64", "amd64"):
        return "amd64"
    if raw in ("aarch64", "arm64"):
        return "arm64"
    return raw


def _detect_shell() -> str:
    shell_env = os.environ.get("SHELL", "")
    if shell_env:
        base = os.path.basename(shell_env).lower()
        if base in ("bash", "zsh", "fish", "sh"):
            return base
    if os.environ.get("PSModulePath"):
        return "powershell"
    if os.environ.get("MSYSTEM"):
        return "bash"
    if os.environ.get("COMSPEC"):
        return "cmd"
    return "unknown"


def _detect_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in platform.release().lower()
    except Exception:  # noqa: BLE001
        return False


def detect_platform() -> Platform:
    """Detect host OS, architecture, shell, WSL, and Git Bash status."""
    return Platform(
        os=_detect_os(), arch=_detect_arch(), shell=_detect_shell(),
        is_wsl=_detect_wsl(), is_git_bash=bool(os.environ.get("MSYSTEM")),
    )


# Per-tool, per-OS install instructions  {tool: {os: hint}}
_HINTS: dict[str, dict[str, str]] = {
    "gh": {
        "linux": "https://cli.github.com/ or: sudo apt install gh",
        "macos": "brew install gh",
        "windows": "winget install --id GitHub.cli  (or https://cli.github.com/)",
    },
    "git": {
        "linux": "sudo apt-get install -y git  (or https://git-scm.com/download/linux)",
        "macos": "xcode-select --install  (or)  brew install git",
        "windows": "https://git-scm.com/download/win",
    },
    "docker": {
        "linux": "curl -fsSL https://get.docker.com | sh",
        "macos": "https://docs.docker.com/desktop/install/mac-install/",
        "windows": "https://docs.docker.com/desktop/install/windows-install/",
    },
    "claude": {
        "linux": "npm install -g @anthropic-ai/claude-code",
        "macos": "npm install -g @anthropic-ai/claude-code",
        "windows": "npm install -g @anthropic-ai/claude-code",
    },
}
_FALLBACK = "See the project README for installation instructions."
_REQUIRED_DEPS = ("gh", "git", "docker", "claude")


def _get_version(name: str) -> str:
    """Run ``<name> --version`` and return the first output line, or ''."""
    import subprocess  # noqa: PLC0415
    try:
        proc = subprocess.run(  # noqa: S603
            [name, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        pass
    return ""


def check_dependencies(plat: Platform | None = None) -> list[DependencyStatus]:
    """Check whether gh, git, docker, and claude are available.

    Returns one :class:`DependencyStatus` per tool with found/version/hint.
    *plat* is auto-detected when ``None``.
    """
    if plat is None:
        plat = detect_platform()
    results: list[DependencyStatus] = []
    for name in _REQUIRED_DEPS:
        path = shutil.which(name) or ""
        found = bool(path)
        version = _get_version(name) if found else ""
        hint = "" if found else _HINTS.get(name, {}).get(plat.os, _FALLBACK)
        results.append(DependencyStatus(name=name, found=found, version=version, path=path, install_hint=hint))
    return results
