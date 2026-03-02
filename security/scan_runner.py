"""Common scan runner utilities — shared infrastructure for security scanners.

Provides :func:`run_tool` for external-tool execution with JSON error handling,
and :func:`create_scan_gate` for standardised gate runner creation.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from factory.gates.framework import GateRunner

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TypeVar

    T = TypeVar("T")

logger = logging.getLogger(__name__)


def run_tool(
    tool: str,
    cmd: list[str],
    parse_fn: Callable[[str], list[T]],
    *,
    cwd: str | None = None,
    timeout: int = 120,
) -> list[T]:
    """Run *tool*, parse its stdout via *parse_fn*, return findings.

    Returns ``[]`` when the tool is missing, produces no output,
    or *parse_fn* raises a JSON / type error.
    """
    if not shutil.which(tool):
        logger.warning("%s not found — skipping", tool)
        return []
    from factory.integrations.shell import run_command  # noqa: PLC0415

    raw = run_command(cmd, cwd=cwd, timeout=timeout).stdout.strip()
    if not raw:
        return []
    try:
        return parse_fn(raw)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
        logger.warning("Failed to parse %s output", tool)
        return []


def create_scan_gate(
    gate_name: str,
    check_name: str,
    check_fn: Callable[[str], tuple[bool, str]],
    workspace: str | Path,
    *,
    metrics_dir: str | Path | None = None,
) -> GateRunner:
    """Build a :class:`GateRunner` that delegates to *check_fn(ws_path)*.

    *check_fn* receives the workspace path string and must return
    ``(passed, message)``.  A ``False`` *passed* raises ``RuntimeError(message)``.
    """
    ws = str(workspace)
    runner = GateRunner(gate_name, metrics_dir=metrics_dir)

    def _check() -> bool | str:
        passed, msg = check_fn(ws)
        if not passed:
            raise RuntimeError(msg)
        return msg

    runner.register_check(check_name, _check)
    return runner
