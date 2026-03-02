"""Console deployment strategy.

Lightweight strategy for CLI tool packaging and release.  Handles
PyPI, npm, and crates.io publishing, version bumping via config files,
and release tagging via git.
"""

from __future__ import annotations

import shutil

from factory.strategies.base import (
    DeployResult,
    PipelineFlags,
    ProvisionResult,
    ReleaseResult,
    StrategyInterface,
    ValidationResult,
    WriteBoundaries,
)

_REGISTRIES = {
    "python": ("pypi", "twine upload dist/*"),
    "node": ("npm", "npm publish"),
    "rust": ("crates.io", "cargo publish"),
}


class ConsoleStrategy(StrategyInterface):
    """Console (CLI tool) deployment strategy."""

    def __init__(
        self,
        app_name: str = "dark-factory",
        ecosystem: str = "python",
    ) -> None:
        self._app = app_name
        self._eco = ecosystem

    # -- Configuration --

    def get_name(self) -> str:
        return "Console"

    def get_overlay_name(self) -> str:
        return "console"

    def get_write_boundaries(self) -> WriteBoundaries:
        return WriteBoundaries(
            allowed_targets=("Local filesystem", "Package registry"),
            requires_approval=False,
            max_parallel_deploys=1,
        )

    def get_agent_count(self) -> int:
        return 1

    def get_pipeline_flags(self) -> PipelineFlags:
        return PipelineFlags(
            parallel_stages=False,
            auto_approve_audit=True,
            coverage_target=80,
            require_manual_review=False,
        )

    def supports_dev_mode(self) -> bool:
        return False

    # -- Operations --

    def deploy(
        self, *, environment: str = "staging", dry_run: bool = False,
    ) -> DeployResult:
        """Build the CLI package (no remote deploy for console)."""
        from factory.integrations.shell import run_command  # noqa: PLC0415

        steps: list[str] = []
        build_cmd = self._build_command()

        if dry_run:
            steps.append(f"[dry-run] Would run: {' '.join(build_cmd)}")
            return DeployResult(True, self.get_endpoint(environment), environment, tuple(steps))

        r = run_command(build_cmd)
        steps.append(f"build: rc={r.returncode}")
        if r.returncode != 0:
            return DeployResult(False, "", environment, (*steps, r.stderr.strip()))

        return DeployResult(True, self.get_endpoint(environment), environment, tuple(steps))

    def validate(self, *, environment: str = "staging") -> ValidationResult:
        """Smoke-test the built CLI package."""
        from factory.integrations.shell import run_command  # noqa: PLC0415

        checks: list[str] = []

        # Verify package was built
        r = run_command(self._check_artifact_command())
        built = r.returncode == 0
        checks.append(f"Package artifact: {'found' if built else 'MISSING'}")

        # Smoke-test: run --version
        r = run_command(self._version_command())
        ver_ok = r.returncode == 0
        checks.append(f"CLI --version: {'OK' if ver_ok else 'FAILED'}")

        return ValidationResult(built and ver_ok, tuple(checks))

    def release(
        self, *, version: str, environment: str = "production",
    ) -> ReleaseResult:
        """Bump version, tag, and publish to the package registry."""
        from factory.integrations.shell import git, run_command  # noqa: PLC0415

        steps: list[str] = []
        tag = f"v{version}"
        registry, publish_cmd = _REGISTRIES.get(self._eco, _REGISTRIES["python"])

        # Build the package
        build = self.deploy(environment=environment)
        steps.extend(build.details)
        if not build.success:
            return ReleaseResult(False, version, tag, tuple(steps))

        # Git tag
        r = git(["tag", "-a", tag, "-m", f"Release {version}"])
        steps.append(f"git tag {tag}: rc={r.returncode}")

        # Publish to registry
        r = run_command(publish_cmd.split())
        steps.append(f"publish to {registry}: rc={r.returncode}")

        # Push tag
        r = git(["push", "origin", tag])
        steps.append(f"git push tag: rc={r.returncode}")

        ok = all("FAILED" not in s for s in steps)
        return ReleaseResult(ok, version, tag, tuple(steps))

    def provision(self, *, dry_run: bool = False) -> ProvisionResult:
        """Provision build tooling (pip/npm/cargo, twine, etc.)."""
        from factory.integrations.shell import run_command  # noqa: PLC0415

        resources: list[str] = []
        steps: list[str] = []
        tools = self._provision_tools()

        for name, install_cmd in tools:
            if dry_run:
                steps.append(f"[dry-run] Would install {name}")
                resources.append(name)
                continue
            if shutil.which(name):
                steps.append(f"{name}: already installed")
                resources.append(name)
                continue
            r = run_command(install_cmd)
            ok = r.returncode == 0
            steps.append(f"{name}: {'installed' if ok else 'FAILED'}")
            if ok:
                resources.append(name)

        return ProvisionResult(
            len(resources) == len(tools), tuple(resources), tuple(steps),
        )

    def bootstrap_deps(self) -> tuple[str, ...]:
        if self._eco == "node":
            return ("node", "npm", "git")
        if self._eco == "rust":
            return ("cargo", "git")
        return ("python", "pip", "twine", "git")

    def get_endpoint(self, environment: str = "staging") -> str:
        registry, _ = _REGISTRIES.get(self._eco, _REGISTRIES["python"])
        if registry == "pypi":
            return f"https://pypi.org/project/{self._app}/"
        if registry == "npm":
            return f"https://www.npmjs.com/package/{self._app}"
        return f"https://crates.io/crates/{self._app}"

    def get_critical_stages(self) -> tuple[str, ...]:
        return ("lint", "unit-test", "build-package", "smoke-test", "publish")

    # -- Private helpers --

    def _build_command(self) -> list[str]:
        if self._eco == "node":
            return ["npm", "run", "build"]
        if self._eco == "rust":
            return ["cargo", "build", "--release"]
        return ["python", "-m", "build"]

    def _check_artifact_command(self) -> list[str]:
        if self._eco == "node":
            return ["test", "-d", "dist"]
        if self._eco == "rust":
            return ["test", "-f", f"target/release/{self._app}"]
        return ["python", "-c", "import pathlib; assert list(pathlib.Path('dist').glob('*'))"]

    def _version_command(self) -> list[str]:
        if self._eco == "node":
            return ["node", "dist/index.js", "--version"]
        if self._eco == "rust":
            return [f"target/release/{self._app}", "--version"]
        return ["python", "-m", self._app, "--version"]

    def _provision_tools(self) -> tuple[tuple[str, list[str]], ...]:
        if self._eco == "node":
            return (("npm", ["npm", "install", "-g", "npm"]),)
        if self._eco == "rust":
            return (("cargo", ["rustup", "update"]),)
        return (
            ("pip", ["python", "-m", "ensurepip", "--upgrade"]),
            ("build", ["pip", "install", "build"]),
            ("twine", ["pip", "install", "twine"]),
        )
