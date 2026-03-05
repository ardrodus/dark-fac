"""Workspace bootstrap result type.

Architecture
~~~~~~~~~~~~
The actual bootstrap is performed by the ``workspace_bootstrap`` DOT pipeline
(``pipelines/workspace_bootstrap.dot``), NOT by Python package-manager logic.
The pipeline uses an LLM agent to detect the runtime, create a scoped
environment (venv, node_modules, etc.), and install project dependencies.

This module provides ONLY the :class:`BootstrapResult` output contract.
The JSON output from the pipeline is parsed into this dataclass by
``orchestrator._bootstrap_workspace_env()``.

**Do NOT add package manager commands, install logic, or platform detection
here.**  If bootstrap behavior needs to change, modify
``pipelines/workspace_bootstrap.dot`` instead.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Outcome of workspace environment bootstrap."""
    runtime_ok: bool = False
    env_created: bool = False
    deps_installed: bool = False
    env_path: str = ""
    errors: tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        return self.runtime_ok and self.deps_installed
