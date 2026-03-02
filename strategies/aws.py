"""AWS deployment strategy.

Cloud-native strategy using ECS/Fargate for compute, CloudFront for
CDN/endpoint, Aurora PostgreSQL for data, and OIDC for CI auth.
Supports staging and production environments.
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

_ENDPOINTS = {
    "staging": "https://staging.{app}.example.com",
    "production": "https://{app}.example.com",
}


class AwsStrategy(StrategyInterface):
    """AWS cloud-native deployment strategy."""

    def __init__(self, app_name: str = "dark-factory") -> None:
        self._app = app_name

    # -- Configuration --

    def get_name(self) -> str:
        return "AWS"

    def get_overlay_name(self) -> str:
        return "aws"

    def get_write_boundaries(self) -> WriteBoundaries:
        return WriteBoundaries(
            allowed_targets=(
                "ECS/Fargate services",
                "CloudFront distributions",
                "S3 static assets",
                "Aurora PostgreSQL clusters",
            ),
            requires_approval=False,
            max_parallel_deploys=4,
        )

    def get_agent_count(self) -> int:
        return 4

    def get_pipeline_flags(self) -> PipelineFlags:
        return PipelineFlags(
            parallel_stages=True,
            auto_approve_audit=True,
            coverage_target=80,
            require_manual_review=False,
        )

    def supports_dev_mode(self) -> bool:
        return True

    # -- Operations --

    def deploy(  # noqa: PLR0912
        self, *, environment: str = "staging", dry_run: bool = False,
    ) -> DeployResult:
        from factory.integrations.shell import run_command  # noqa: PLC0415

        steps: list[str] = []
        cluster = f"{self._app}-{environment}"
        service = f"{self._app}-svc"
        ecr = f"123456789.dkr.ecr.us-east-1.amazonaws.com/{self._app}"

        if dry_run:
            steps.append(f"[dry-run] Would push image to {ecr}")
            steps.append(f"[dry-run] Would update ECS service {service} on {cluster}")
            return DeployResult(True, self.get_endpoint(environment), environment, tuple(steps))

        r = run_command(["docker", "build", "-t", f"{self._app}:latest", "."])
        steps.append(f"docker build: rc={r.returncode}")
        if r.returncode != 0:
            return DeployResult(False, "", environment, (*steps, r.stderr.strip()))

        r = run_command(["docker", "tag", f"{self._app}:latest", f"{ecr}:latest"])
        steps.append(f"docker tag: rc={r.returncode}")

        r = run_command(["docker", "push", f"{ecr}:latest"])
        steps.append(f"docker push: rc={r.returncode}")
        if r.returncode != 0:
            return DeployResult(False, "", environment, (*steps, r.stderr.strip()))

        r = run_command([
            "aws", "ecs", "update-service", "--cluster", cluster,
            "--service", service, "--force-new-deployment",
        ])
        steps.append(f"ecs update-service: rc={r.returncode}")
        ok = r.returncode == 0
        return DeployResult(ok, self.get_endpoint(environment) if ok else "", environment, tuple(steps))

    def validate(self, *, environment: str = "staging") -> ValidationResult:
        from factory.integrations.shell import run_command  # noqa: PLC0415

        checks: list[str] = []
        cluster = f"{self._app}-{environment}"

        r = run_command([
            "aws", "ecs", "describe-services",
            "--cluster", cluster, "--services", f"{self._app}-svc",
        ])
        ecs_ok = r.returncode == 0 and "ACTIVE" in r.stdout
        checks.append(f"ECS service: {'ACTIVE' if ecs_ok else 'FAILED'}")

        r = run_command(["aws", "cloudfront", "list-distributions"])
        cf_ok = r.returncode == 0
        checks.append(f"CloudFront: {'OK' if cf_ok else 'FAILED'}")

        r = run_command([
            "aws", "rds", "describe-db-clusters",
            "--db-cluster-identifier", f"{self._app}-{environment}",
        ])
        db_ok = r.returncode == 0 and "available" in r.stdout
        checks.append(f"Aurora: {'available' if db_ok else 'FAILED'}")

        return ValidationResult(ecs_ok and cf_ok and db_ok, tuple(checks))

    def release(
        self, *, version: str, environment: str = "production",
    ) -> ReleaseResult:
        steps: list[str] = []
        tag = f"v{version}"

        result = self.deploy(environment=environment)
        steps.extend(result.details)

        if result.success:
            from factory.integrations.shell import run_command  # noqa: PLC0415

            r = run_command([
                "aws", "cloudfront", "create-invalidation",
                "--distribution-id", f"{self._app}-dist", "--paths", "/*",
            ])
            steps.append(f"CloudFront invalidation: rc={r.returncode}")

        return ReleaseResult(result.success, version, tag, tuple(steps))

    def provision(self, *, dry_run: bool = False) -> ProvisionResult:
        from factory.integrations.shell import run_command  # noqa: PLC0415

        resources: list[str] = []
        steps: list[str] = []
        infra = (
            ("ECS cluster", [
                "aws", "ecs", "create-cluster",
                "--cluster-name", f"{self._app}-staging",
            ]),
            ("Aurora cluster", [
                "aws", "rds", "create-db-cluster",
                "--db-cluster-identifier", f"{self._app}-staging",
                "--engine", "aurora-postgresql",
            ]),
            ("CloudFront distribution", [
                "aws", "cloudfront", "create-distribution",
                "--origin-domain-name", f"{self._app}.s3.amazonaws.com",
            ]),
            ("OIDC provider", [
                "aws", "iam", "create-open-id-connect-provider",
                "--url", "https://token.actions.githubusercontent.com",
            ]),
        )
        for name, cmd in infra:
            if dry_run:
                steps.append(f"[dry-run] Would create {name}")
                resources.append(name)
                continue
            r = run_command(cmd)
            ok = r.returncode == 0
            steps.append(f"{name}: {'created' if ok else 'FAILED'} (rc={r.returncode})")
            if ok:
                resources.append(name)

        return ProvisionResult(len(resources) == len(infra), tuple(resources), tuple(steps))

    def bootstrap_deps(self) -> tuple[str, ...]:
        return ("aws", "docker", "git")

    def get_endpoint(self, environment: str = "staging") -> str:
        tpl = _ENDPOINTS.get(environment, _ENDPOINTS["staging"])
        return tpl.format(app=self._app)

    def get_critical_stages(self) -> tuple[str, ...]:
        return (
            "build",
            "unit-test",
            "security-scan",
            "deploy-staging",
            "integration-test",
            "deploy-production",
        )
