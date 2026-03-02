"""Bridge for calling not-yet-migrated bash functions from Python.

During the incremental migration, some modules still live in bash.  This
module provides :func:`call_bash_function` so that migrated Python code
can invoke those bash functions via subprocess and get structured results.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dark_factory.integrations.shell import CommandResult, run_command

logger = logging.getLogger(__name__)

_FACTORY_ROOT = Path(__file__).resolve().parents[2]
_DARK_FACTORY_SH = _FACTORY_ROOT / "factory" / "scripts" / "dark-factory.sh"


def _resolve_script_path() -> str:
    """Return the absolute path to *dark-factory.sh*."""
    env_override = os.environ.get("DARK_FACTORY_SH")
    if env_override:
        return env_override
    return str(_DARK_FACTORY_SH)


def call_bash_function(
    function_name: str,
    args: list[str] | None = None,
    *,
    timeout: float = 60,
    check: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Invoke a bash function defined in *dark-factory.sh*.

    Parameters
    ----------
    function_name:
        Name of the bash function to call (e.g. ``"delegate_to_python"``).
    args:
        Positional arguments forwarded to the bash function.
    timeout:
        Seconds before the subprocess is killed.
    check:
        Raise :class:`~factory.integrations.shell.CommandError` on non-zero exit.
    cwd:
        Working directory for the child process.
    env:
        Full environment mapping; inherits the parent env when ``None``.
    """
    script = _resolve_script_path()
    cmd = [
        "bash",
        "-c",
        f'source "{script}" && {function_name} {_shell_join(args or [])}',
    ]
    logger.debug("bash_bridge: calling %s(%s)", function_name, args)
    return run_command(cmd, timeout=timeout, check=check, cwd=cwd, env=env)


def _shell_join(args: list[str]) -> str:
    """Join arguments with proper quoting for bash."""
    import shlex

    return " ".join(shlex.quote(a) for a in args)
