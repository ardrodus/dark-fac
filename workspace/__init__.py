"""Workspace management package.

Backward-compatibility aliases — import from ``factory.workspace`` instead
of ``factory.workspace.manager`` directly.  These aliases will be removed
after one release cycle.
"""

from factory.workspace.manager import (
    WorkspaceInfo,
    WorkspaceResult,
    cache_workspace,
    clean_all_workspaces,
    clean_workspace,
    create_workspace,
    get_workspace,
    list_workspaces,
)

__all__ = [
    "WorkspaceInfo",
    "WorkspaceResult",
    "cache_workspace",
    "clean_all_workspaces",
    "clean_workspace",
    "create_workspace",
    "get_workspace",
    "list_workspaces",
]
