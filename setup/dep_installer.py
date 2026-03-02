"""Install project-specific tools detected during analysis."""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dark_factory.setup.project_analyzer import AnalysisResult


@dataclass(frozen=True, slots=True)
class InstallResult:
    """Counts of installed, skipped, and failed tools."""
    installed: int = 0
    skipped: int = 0
    failed: int = 0
    details: tuple[tuple[str, str], ...] = ()  # (tool, status) pairs


def _apt(pkg: str) -> list[str]:
    return ["sudo", "apt-get", "install", "-y", pkg]


def _wg(wid: str) -> list[str]:
    return ["winget", "install", "--id", wid, "-e"]


# {tool: {pm: cmd}} — "apt"/"brew"/"scoop"/"winget" or "_special" for aliases
_CMDS: dict[str, dict[str, list[str]]] = {
    "cargo": {"_special": ["rustup"]}, "rustc": {"_special": ["rustup"]},
    "pip": {"_special": ["python"]}, "npm": {"_special": ["node"]},
    "bundle": {"gem": ["gem", "install", "bundler"]},
    "go": {"apt": _apt("golang-go"), "brew": ["brew", "install", "go"],
            "scoop": ["scoop", "install", "go"], "winget": _wg("GoLang.Go")},
    "python": {"apt": ["sudo", "apt-get", "install", "-y", "python3", "python3-pip"],
               "brew": ["brew", "install", "python3"], "scoop": ["scoop", "install", "python"],
               "winget": _wg("Python.Python.3.12")},
    "node": {"apt": ["sudo", "apt-get", "install", "-y", "nodejs", "npm"],
             "brew": ["brew", "install", "node"], "scoop": ["scoop", "install", "nodejs"],
             "winget": _wg("OpenJS.NodeJS.LTS")},
    "java": {"apt": _apt("default-jdk"), "brew": ["brew", "install", "openjdk"],
             "scoop": ["scoop", "install", "openjdk"], "winget": _wg("EclipseAdoptium.Temurin.21.JDK")},
    "mvn": {"apt": _apt("maven"), "brew": ["brew", "install", "maven"],
            "scoop": ["scoop", "install", "maven"]},
    "dotnet": {"apt": _apt("dotnet-sdk-8.0"), "brew": ["brew", "install", "--cask", "dotnet-sdk"],
               "winget": _wg("Microsoft.DotNet.SDK.8")},
    "gcc": {"apt": _apt("gcc"), "brew": ["brew", "install", "gcc"], "scoop": ["scoop", "install", "gcc"]},
    "make": {"apt": _apt("make"), "brew": ["brew", "install", "make"], "scoop": ["scoop", "install", "make"]},
    "ruby": {"apt": _apt("ruby-full"), "brew": ["brew", "install", "ruby"],
             "scoop": ["scoop", "install", "ruby"]},
}

_PM_PRIORITY: dict[str, tuple[str, ...]] = {
    "linux": ("apt", "brew"), "macos": ("brew",), "windows": ("scoop", "winget"),
}


def _detect_pm(plat_os: str) -> str:
    for pm in _PM_PRIORITY.get(plat_os, ()):
        if shutil.which(pm) or (pm == "apt" and shutil.which("apt-get")):
            return pm
    return ""


def _is_installed(tool: str) -> bool:
    return bool(shutil.which(tool))


def _run_install(cmd: list[str]) -> bool:
    import subprocess  # noqa: PLC0415
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)  # noqa: S603
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _install_tool(tool: str, plat_os: str) -> bool:
    cmds = _CMDS.get(tool)
    if not cmds:
        return False
    if "_special" in cmds:
        dep = cmds["_special"][0]
        if dep == "rustup":
            if _is_installed("rustup"):
                return _run_install(["rustup", "update", "stable"])
            if plat_os == "windows":
                return False  # rustup-init.exe requires interactive install
            return _run_install(["sh", "-c",
                                 "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"])
        if dep == "python":
            return _is_installed("python3") or _is_installed("python") or _install_tool("python", plat_os)
        if dep == "node":
            return _is_installed("node") or _install_tool("node", plat_os)
        return False
    pm = _detect_pm(plat_os)
    if not pm or pm not in cmds:
        return False
    return _run_install(cmds[pm])


def install_project_deps(analysis: AnalysisResult, *, plat_os: str = "") -> InstallResult:
    """Install project-specific tools from *analysis.required_tools*."""
    tools = analysis.required_tools
    if not tools:
        sys.stdout.write("  No project-specific tools required.\n")
        return InstallResult()
    if not plat_os:
        from dark_factory.setup.platform import detect_platform  # noqa: PLC0415
        plat_os = detect_platform().os
    w = sys.stdout.write
    w("\n  Checking project dependencies...\n\n")
    installed, skipped, failed = 0, 0, 0
    details: list[tuple[str, str]] = []
    for tool in tools:
        if _is_installed(tool):
            w(f"  + {tool}: found\n")
            skipped += 1
            details.append((tool, "skipped"))
            continue
        w(f"  > {tool}: installing...\n")
        if _install_tool(tool, plat_os):
            if _is_installed(tool):
                w(f"  + {tool}: installed\n")
                installed += 1
                details.append((tool, "installed"))
            else:
                w(f"  x {tool}: install succeeded but not in PATH\n")
                failed += 1
                details.append((tool, "failed"))
        else:
            w(f"  x {tool}: failed\n")
            failed += 1
            details.append((tool, "failed"))
    w(f"\n  Summary: {installed} installed, {skipped} skipped, {failed} failed\n")
    return InstallResult(installed=installed, skipped=skipped, failed=failed, details=tuple(details))
