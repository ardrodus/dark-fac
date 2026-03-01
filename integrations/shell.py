"""Safe, typed wrapper for shelling out to gh, git, docker, and other CLI tools."""

from __future__ import annotations

import logging
import platform
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"
_DEFAULT_TIMEOUT = 60

# GitHub CLI rate-limit: retry once after a short back-off.
_GH_RATE_LIMIT_EXIT_CODE = 4
_GH_RATE_LIMIT_WAIT_SECONDS = 5.0
_GH_MAX_RETRIES = 1


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Typed result of a subprocess invocation."""

    stdout: str
    stderr: str
    returncode: int
    duration_ms: float


class CommandError(Exception):
    """Raised when a command exits non-zero and *check* mode is enabled."""

    def __init__(self, result: CommandResult, cmd: list[str]) -> None:
        self.result = result
        self.cmd = cmd
        super().__init__(
            f"Command {cmd!r} exited with code {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def _run_subprocess(
    cmd: list[str],
    *,
    timeout: float,
    cwd: str | None,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    """Invoke ``subprocess.run`` with platform-appropriate flags."""
    if _IS_WINDOWS:
        return subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )


def run_command(
    cmd: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    check: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run *cmd* via :func:`subprocess.run` and return a :class:`CommandResult`.

    Parameters
    ----------
    cmd:
        Command and arguments as a list of strings.
    timeout:
        Maximum wall-clock seconds before the process is killed (default 60).
    check:
        If ``True``, raise :class:`CommandError` on a non-zero exit code.
    cwd:
        Working directory for the child process.
    env:
        Full environment mapping. When ``None`` the parent env is inherited.
    """
    logger.debug("run_command: %r (timeout=%s, cwd=%s)", cmd, timeout, cwd)

    start = time.monotonic()
    try:
        proc = _run_subprocess(cmd, timeout=timeout, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        duration_ms = (time.monotonic() - start) * 1000
        logger.warning("Command timed out after %.0f ms: %r", duration_ms, cmd)
        result = CommandResult(stdout="", stderr="timeout", returncode=-1, duration_ms=duration_ms)
        if check:
            raise CommandError(result, cmd) from None
        return result

    duration_ms = (time.monotonic() - start) * 1000
    result = CommandResult(
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
        duration_ms=duration_ms,
    )
    logger.debug(
        "run_command done: rc=%d, duration=%.0f ms, cmd=%r",
        result.returncode,
        result.duration_ms,
        cmd,
    )
    if check and result.returncode != 0:
        raise CommandError(result, cmd)
    return result


# ── convenience wrappers ────────────────────────────────────────────


def gh(
    args: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    check: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run a ``gh`` (GitHub CLI) command with rate-limit retry logic."""
    cmd = ["gh", *args]
    result = run_command(cmd, timeout=timeout, check=False, cwd=cwd, env=env)

    if result.returncode == _GH_RATE_LIMIT_EXIT_CODE:
        for attempt in range(1, _GH_MAX_RETRIES + 1):
            logger.info(
                "gh rate-limited (attempt %d/%d). Waiting %.1f s …",
                attempt,
                _GH_MAX_RETRIES,
                _GH_RATE_LIMIT_WAIT_SECONDS,
            )
            time.sleep(_GH_RATE_LIMIT_WAIT_SECONDS)
            result = run_command(cmd, timeout=timeout, check=False, cwd=cwd, env=env)
            if result.returncode != _GH_RATE_LIMIT_EXIT_CODE:
                break

    if check and result.returncode != 0:
        raise CommandError(result, cmd)
    return result


def git(
    args: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    check: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run a ``git`` command."""
    return run_command(["git", *args], timeout=timeout, check=check, cwd=cwd, env=env)


def docker(
    args: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    check: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run a ``docker`` command."""
    return run_command(["docker", *args], timeout=timeout, check=check, cwd=cwd, env=env)
