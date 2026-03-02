"""Compose fragment merging — unified docker-compose.yml from twin configs (US-706).

Port of ``compose-merge.sh``.  Collects twin compose fragments and produces
a single ``docker-compose.yml`` with shared networking and depends_on.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_COMPOSE_VERSION = "3.8"
_NETWORK = "df-net"


def _service_name(fragment: str) -> str:
    """Extract the first service name from a compose service block."""
    for line in fragment.splitlines():
        m = re.match(r"^  ([\w][\w.-]*):$", line)
        if m:
            return m.group(1)
    return ""


def _inject_network(fragment: str) -> str:
    """Append ``df-net`` network to a service block if absent."""
    if "networks:" in fragment:
        return fragment
    return fragment.rstrip("\n") + f"\n    networks:\n      - {_NETWORK}\n"


def _depends_on_block(svc_names: list[str]) -> list[str]:
    """Build a ``depends_on`` YAML block with ``service_healthy`` conditions."""
    lines = ["    depends_on:"]
    for n in svc_names:
        lines.append(f"      {n}:")
        lines.append("        condition: service_healthy")
    return lines


def _env_overrides(fragments: list[Any]) -> dict[str, str]:
    """Collect all ``env_overrides`` from fragment configs."""
    env: dict[str, str] = {}
    for cfg in fragments:
        overrides = getattr(cfg, "env_overrides", None)
        if isinstance(overrides, dict):
            env.update(overrides)
    return env


def merge_compose(
    fragments: list[Any],
    project_compose: str | None = None,
) -> str:
    """Merge twin compose fragments into a unified ``docker-compose.yml``.

    Each element of *fragments* must expose a ``compose_fragment`` attribute
    (both :class:`~factory.twins.api_twin_gen.TwinConfig` and
    :class:`~factory.twins.db_twin_gen.DbTwinConfig` satisfy this).

    Args:
        fragments: Twin config objects with ``compose_fragment`` strings.
        project_compose: Optional existing ``docker-compose.yml`` content.

    Returns:
        Complete ``docker-compose.yml`` content as a string.
    """
    svc_blocks: list[str] = []
    svc_names: list[str] = []

    for cfg in fragments:
        raw: str = getattr(cfg, "compose_fragment", "")
        if not raw.strip():
            continue
        name = _service_name(raw) or getattr(cfg, "service_name", "") or ""
        if name:
            svc_names.append(name)
        svc_blocks.append(_inject_network(raw))

    if not svc_blocks:
        return project_compose or f'version: "{_COMPOSE_VERSION}"\nservices: {{}}\n'

    if project_compose and project_compose.strip():
        return _merge_into(project_compose, svc_blocks, svc_names)

    # ── Fresh compose ──
    lines: list[str] = [f'version: "{_COMPOSE_VERSION}"', "", "services:"]
    for blk in svc_blocks:
        lines.append(blk.rstrip("\n"))

    # Env override hints for app container
    env = _env_overrides(fragments)
    if env:
        lines.append("")
        lines.append("  # Environment overrides for app container:")
        for k, v in env.items():
            lines.append(f"  #   {k}={v}")

    lines.extend(["", "networks:", f"  {_NETWORK}:", "    driver: bridge", ""])
    logger.info("Merged %d twin fragment(s) into docker-compose.yml", len(svc_blocks))
    return "\n".join(lines) + "\n"


def _merge_into(
    base: str, svc_blocks: list[str], svc_names: list[str],
) -> str:
    """Inject twin services into an existing compose file."""
    blines = base.rstrip("\n").splitlines()

    # Insert before top-level networks/volumes sections
    insert = len(blines)
    for i, line in enumerate(blines):
        if re.match(r"^(networks|volumes):", line):
            insert = i
            break

    before = blines[:insert]
    after = blines[insert:]

    injected: list[str] = []
    for blk in svc_blocks:
        injected.append(blk.rstrip("\n"))

    result_lines = before + injected

    # Ensure df-net network in the trailing section
    full_after = "\n".join(after)
    if _NETWORK not in full_after and _NETWORK not in "\n".join(before):
        if any(ln.startswith("networks:") for ln in after):
            after.extend([f"  {_NETWORK}:", "    driver: bridge"])
        else:
            after.extend(["", "networks:", f"  {_NETWORK}:", "    driver: bridge"])

    result_lines.extend(after)
    return "\n".join(result_lines) + "\n"
