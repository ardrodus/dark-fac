"""Container network isolation — egress policy, compose validation, proxy config.

Ported from ``network-isolation.sh`` (US-018 / US-611).  Container network lockdown
during Crucible execution: egress policy, compose validation, internal Docker
network, proxy config, blocked attempt audit logging.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from dark_factory.integrations.shell import docker

if TYPE_CHECKING:
    from dark_factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_PROXY_PORT, _PROXY_IMAGE = 3128, "ubuntu/squid:latest"
_POLICY_FILE, _BLOCKED_LOG = "egress-policy.json", "blocked-egress.log"

_DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = (
    "github.com", "api.github.com", "registry.npmjs.org", "pypi.org",
    "files.pythonhosted.org", "crates.io", "static.crates.io",
    "proxy.golang.org", "sum.golang.org", "storage.googleapis.com",
    "repo1.maven.org", "rubygems.org", "registry.yarnpkg.com",
    "docker.io", "registry-1.docker.io", "auth.docker.io",
    "production.cloudflare.docker.com",
)

# ── Data models ──────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    """Egress policy controlling which domains containers may reach."""
    allow_all_egress: bool = False
    allowed_domains: tuple[str, ...] = _DEFAULT_ALLOWED_DOMAINS
    blocked_domains: tuple[str, ...] = ()
    proxy_image: str = _PROXY_IMAGE


@dataclass(frozen=True, slots=True)
class Violation:
    """A single compose-file security violation."""
    check: str
    severity: str
    file: str
    line: int
    description: str
    recommendation: str


# ── Helpers ──────────────────────────────────────────────────────

def _security_dir(workspace: Workspace) -> Path:
    d = Path(workspace.path) / ".dark-factory" / "security"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Policy generation ────────────────────────────────────────────

def generate_default_policy(workspace: Workspace) -> NetworkPolicy:
    """Create a default egress policy, persist to workspace, and return it."""
    policy = NetworkPolicy()
    payload = {"allow_all_egress": policy.allow_all_egress,
               "allowed_domains": list(policy.allowed_domains),
               "blocked_domains": list(policy.blocked_domains),
               "proxy_image": policy.proxy_image}
    path = _security_dir(workspace) / _POLICY_FILE
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("Default egress policy generated at %s", path)
    return policy


def load_policy(workspace: Workspace) -> NetworkPolicy:
    """Load egress policy from disk; generates default if missing."""
    policy_path = _security_dir(workspace) / _POLICY_FILE
    if not policy_path.is_file():
        return generate_default_policy(workspace)
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load egress policy: %s", exc)
        return NetworkPolicy()
    if not isinstance(data, dict):
        return NetworkPolicy()
    return NetworkPolicy(
        allow_all_egress=bool(data.get("allow_all_egress", False)),
        allowed_domains=tuple(data.get("allowed_domains", [])),
        blocked_domains=tuple(data.get("blocked_domains", [])),
        proxy_image=str(data.get("proxy_image", _PROXY_IMAGE)),
    )


# ── Compose validation ───────────────────────────────────────────

_CHECKS: tuple[tuple[str, str, str, str], ...] = (
    (r"docker\.sock", "docker_socket",
     "Docker socket mount detected",
     "NEVER mount /var/run/docker.sock -- this allows container escape"),
    (r"privileged:\s*true", "privileged_mode",
     "Privileged mode enabled -- grants full host access",
     "Remove 'privileged: true' -- use specific capabilities instead"),
    (r"network_mode:\s*.*host", "host_network",
     "Host network mode bypasses network isolation",
     "Remove 'network_mode: host' -- use bridge networks with explicit port mapping"),
)


def validate_compose(compose_path: Path, policy: NetworkPolicy) -> list[Violation]:
    """Check a docker-compose file for security violations.

    Checks are policy-independent (socket/privileged/host-network always block).
    """
    if not compose_path.is_file():
        return []
    lines = compose_path.read_text(encoding="utf-8").splitlines()
    violations: list[Violation] = []
    for pattern, check_name, desc, rec in _CHECKS:
        regex = re.compile(pattern)
        for idx, line in enumerate(lines, 1):
            if regex.search(line):
                violations.append(Violation(
                    check=check_name, severity="BLOCK", file=str(compose_path),
                    line=idx, description=f"{desc}: {line.strip()}", recommendation=rec))
    return violations


# ── Internal Docker network ──────────────────────────────────────


def create_internal_network(name: str) -> bool:
    """Create ``--internal`` Docker network ``df-project-<name>-net``.

    Returns ``True`` on success or if the network already exists.
    """
    net_name = f"df-project-{name}-net"
    if not shutil.which("docker"):
        logger.warning("Docker not available -- skipping network creation")
        return True
    if docker(["network", "inspect", net_name]).returncode == 0:
        logger.info("Internal network '%s' already exists", net_name)
        return True
    if docker(["network", "create", "--internal", net_name]).returncode == 0:
        logger.info("Created internal network '%s'", net_name)
        return True
    logger.error("Failed to create internal network '%s'", net_name)
    return False


def remove_internal_network(name: str) -> None:
    """Tear down internal Docker network ``df-project-<name>-net``."""
    net_name = f"df-project-{name}-net"
    if not shutil.which("docker"):
        return
    if docker(["network", "inspect", net_name]).returncode == 0:
        docker(["network", "rm", net_name])
        logger.info("Removed internal network '%s'", net_name)


# ── Proxy configuration ─────────────────────────────────────────


def generate_proxy_config(policy: NetworkPolicy) -> str:
    """Generate squid proxy configuration for allowlisted external access."""
    parts = ["# Auto-generated by Dark Factory -- do not edit manually",
             f"http_port {_PROXY_PORT}", ""]
    if policy.allow_all_egress:
        parts += ["# allow_all_egress: true -- permissive mode", "http_access allow all"]
    else:
        parts.append("# allow_all_egress: false -- whitelist mode")
        if policy.blocked_domains:
            acl = " ".join(f".{d}" for d in policy.blocked_domains)
            parts += [f"acl blocked_domains dstdomain {acl}", "http_access deny blocked_domains", ""]
        if policy.allowed_domains:
            acl = " ".join(f".{d}" for d in policy.allowed_domains)
            parts += [f"acl allowed_domains dstdomain {acl}", "http_access allow allowed_domains"]
        parts += ["", "# Block everything else", "http_access deny all"]
    parts += ["", "# Logging -- blocked attempts are monitored",
              "access_log stdio:/var/log/squid/access.log",
              "cache_log stdio:/var/log/squid/cache.log"]
    return "\n".join(parts) + "\n"


# ── Blocked-attempt logging ──────────────────────────────────────


def log_blocked_attempt(domain: str, workspace: Workspace, *, container: str = "unknown") -> None:
    """Append a blocked egress attempt to the security audit log."""
    log_file = _security_dir(workspace) / _BLOCKED_LOG
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(f"{ts} container={container} domain={domain} action=BLOCKED\n")
    logger.warning("Blocked egress attempt: container=%s domain=%s", container, domain)