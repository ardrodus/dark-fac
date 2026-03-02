"""On-premises deployment strategy.

Safety-first strategy using Docker Compose for local orchestration,
SSH-based host management, health-check polling, and rolling restarts
across a fleet of self-hosted machines.
"""

from __future__ import annotations

from factory.strategies.base import (
    DeployResult,
    PipelineFlags,
    ProvisionResult,
    ReleaseResult,
    StrategyInterface,
    ValidationResult,
    WriteBoundaries,
)

_DEFAULT_HOSTS = ("host-1.internal", "host-2.internal")
_HEALTH_PATH = "/healthz"


class OnPremStrategy(StrategyInterface):
    """On-premises deployment strategy."""

    def __init__(
        self,
        app_name: str = "dark-factory",
        hosts: tuple[str, ...] = _DEFAULT_HOSTS,
    ) -> None:
        self._app = app_name
        self._hosts = hosts

    # -- Configuration --

    def get_name(self) -> str:
        return "On-Premises"

    def get_overlay_name(self) -> str:
        return "on-prem"

    def get_write_boundaries(self) -> WriteBoundaries:
        return WriteBoundaries(
            allowed_targets=(
                "Docker Compose services",
                "SSH-managed hosts",
            ),
            requires_approval=True,
            max_parallel_deploys=1,
        )

    def get_agent_count(self) -> int:
        return 2

    def get_pipeline_flags(self) -> PipelineFlags:
        return PipelineFlags(
            parallel_stages=False,
            auto_approve_audit=False,
            coverage_target=95,
            require_manual_review=True,
        )

    def supports_dev_mode(self) -> bool:
        return False

    # -- Operations --

    def deploy(
        self, *, environment: str = "staging", dry_run: bool = False,
    ) -> DeployResult:
        from factory.integrations.shell import run_command  # noqa: PLC0415

        steps: list[str] = []
        compose_file = f"docker-compose.{environment}.yml"

        if dry_run:
            steps.append(f"[dry-run] Would run docker compose -f {compose_file} up -d")
            for h in self._hosts:
                steps.append(f"[dry-run] Would deploy to {h} via SSH")
            return DeployResult(True, self.get_endpoint(environment), environment, tuple(steps))

        # Build images locally
        r = run_command(["docker", "compose", "-f", compose_file, "build"])
        steps.append(f"compose build: rc={r.returncode}")
        if r.returncode != 0:
            return DeployResult(False, "", environment, (*steps, r.stderr.strip()))

        # Rolling deploy to each host
        all_ok = True
        for host in self._hosts:
            r = run_command([
                "ssh", host,
                f"cd /opt/{self._app} && docker compose -f {compose_file} pull && "
                f"docker compose -f {compose_file} up -d",
            ])
            ok = r.returncode == 0
            steps.append(f"deploy {host}: {'OK' if ok else 'FAILED'}")
            if not ok:
                all_ok = False
                break  # stop rolling deploy on first failure

        ep = self.get_endpoint(environment) if all_ok else ""
        return DeployResult(all_ok, ep, environment, tuple(steps))

    def validate(self, *, environment: str = "staging") -> ValidationResult:
        from factory.integrations.shell import run_command  # noqa: PLC0415

        checks: list[str] = []

        # Check Docker containers on each host
        for host in self._hosts:
            r = run_command([
                "ssh", host,
                f"docker compose -f docker-compose.{environment}.yml ps --format json",
            ])
            up = r.returncode == 0 and "running" in r.stdout.lower()
            checks.append(f"{host} containers: {'running' if up else 'FAILED'}")

        # Health-check each host
        for host in self._hosts:
            r = run_command(["curl", "-sf", f"http://{host}:8080{_HEALTH_PATH}"])
            healthy = r.returncode == 0
            checks.append(f"{host} health: {'OK' if healthy else 'FAILED'}")

        passed = all("FAILED" not in c for c in checks)
        return ValidationResult(passed, tuple(checks))

    def release(
        self, *, version: str, environment: str = "production",
    ) -> ReleaseResult:
        steps: list[str] = []
        tag = f"v{version}"

        # Rolling restart across all hosts
        result = self.deploy(environment=environment)
        steps.extend(result.details)

        if result.success:
            # Verify health after rolling restart
            val = self.validate(environment=environment)
            steps.extend(val.checks)
            if not val.passed:
                return ReleaseResult(False, version, tag, tuple(steps))

        return ReleaseResult(result.success, version, tag, tuple(steps))

    def provision(self, *, dry_run: bool = False) -> ProvisionResult:
        from factory.integrations.shell import run_command  # noqa: PLC0415

        resources: list[str] = []
        steps: list[str] = []

        for host in self._hosts:
            if dry_run:
                steps.append(f"[dry-run] Would provision Docker on {host}")
                resources.append(host)
                continue
            # Install Docker and docker-compose on the host
            r = run_command([
                "ssh", host,
                "which docker || curl -fsSL https://get.docker.com | sh",
            ])
            ok = r.returncode == 0
            steps.append(f"Docker on {host}: {'ready' if ok else 'FAILED'}")
            # Create application directory
            r2 = run_command(["ssh", host, f"mkdir -p /opt/{self._app}"])
            steps.append(f"App dir on {host}: {'created' if r2.returncode == 0 else 'FAILED'}")
            if ok and r2.returncode == 0:
                resources.append(host)

        return ProvisionResult(
            len(resources) == len(self._hosts), tuple(resources), tuple(steps),
        )

    def bootstrap_deps(self) -> tuple[str, ...]:
        return ("docker", "ssh", "curl", "git")

    def get_endpoint(self, environment: str = "staging") -> str:
        host = self._hosts[0] if self._hosts else "localhost"
        port = "8080" if environment == "staging" else "80"
        return f"http://{host}:{port}"

    def get_critical_stages(self) -> tuple[str, ...]:
        return (
            "build",
            "unit-test",
            "security-scan",
            "deploy",
            "health-check",
            "manual-approval",
        )
