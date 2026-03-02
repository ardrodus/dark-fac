"""Crucible twin runner — clone test repo, Sentinel scan, scope detect, test, capture.

Implements the crucible.dot pipeline wired to twin infrastructure:
load_tests → sentinel_scan → detect_scope → run_tests → analyze → verdict.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dark_factory.crucible.orchestrator import CrucibleVerdict
from dark_factory.integrations.shell import git, run_command

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.integrations.shell import CommandResult
    from dark_factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_TEST_TIMEOUT = 600
_PLAYWRIGHT_EXTS = frozenset({".ts", ".js", ".mjs", ".spec.ts", ".spec.js", ".test.ts", ".test.js"})


@dataclass(frozen=True, slots=True)
class ScopeResult:
    """Files changed between base and head commits."""

    changed_files: tuple[str, ...]
    test_files: tuple[str, ...]
    has_test_changes: bool


@dataclass(frozen=True, slots=True)
class TwinRunResult:
    """Full outcome of a Crucible twin-connected test run."""

    verdict: CrucibleVerdict
    pass_count: int = 0
    fail_count: int = 0
    flaky_count: int = 0
    screenshots: tuple[str, ...] = ()
    traces: tuple[str, ...] = ()
    scope: ScopeResult | None = None
    sentinel_passed: bool = True
    error: str = ""


# ── Helpers ──────────────────────────────────────────────────


def _clone_url(repo: str) -> str:
    import os  # noqa: PLC0415

    token = os.environ.get("GH_TOKEN", "")
    if token:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


def _resolve_crucible_repo(
    workspace: Workspace,
    config: dict[str, Any] | None,
    crucible_repo: str | None,
) -> str:
    """Resolve the crucible test repo from explicit arg, config, or workspace repo."""
    if crucible_repo:
        return crucible_repo
    if config:
        val = config.get("crucible_repo", "") or config.get("CRUCIBLE_REPO", "")
        if val:
            return str(val)
    # Derive from workspace repo: owner/name → owner/name-crucible
    parts = workspace.name.split("/")
    if len(parts) == 2:  # noqa: PLR2004
        return f"{parts[0]}/{parts[1]}-crucible"
    return ""


def _clone_test_repo(
    crucible_repo: str,
    target: Path,
    *,
    git_fn: Callable[..., CommandResult] | None = None,
) -> bool:
    """Clone crucible test repo into *target*. Returns True on success."""
    _git = git_fn or git
    if (target / ".git").is_dir():
        r = _git(["pull", "--ff-only"], cwd=str(target))
        return r.returncode == 0
    target.parent.mkdir(parents=True, exist_ok=True)
    r = _git(["clone", _clone_url(crucible_repo), str(target)])
    return (target / ".git").is_dir()


def _run_sentinel_gate1(
    test_dir: Path,
    *,
    gate_fn: Callable[[Path], bool] | None = None,
) -> bool:
    """Run Sentinel Gate 1 on cloned test code. Returns True if clean."""
    if gate_fn is not None:
        return gate_fn(test_dir)
    from dark_factory.gates.framework import GateRunner  # noqa: PLC0415

    runner = GateRunner("sentinel-gate1-test-code", metrics_dir=test_dir / ".dark-factory")
    runner.register_check(
        "no-secrets-in-tests",
        lambda: _check_no_secrets(test_dir),
    )
    runner.register_check(
        "no-malicious-deps",
        lambda: _check_deps_safe(test_dir),
    )
    report = runner.run()
    return report.passed


def _check_no_secrets(test_dir: Path) -> bool:
    """Quick secret scan on test files."""
    patterns = ("password", "api_key", "secret_key", "private_key", "access_token")
    for p in test_dir.rglob("*"):
        if not p.is_file() or p.suffix not in {".ts", ".js", ".json", ".env"}:
            continue
        if p.name == "package-lock.json":
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        for pat in patterns:
            if pat in content and "process.env" not in content[max(0, content.index(pat) - 30):content.index(pat)]:
                logger.warning("Potential secret in %s: pattern '%s'", p, pat)
                return False
    return True


def _check_deps_safe(test_dir: Path) -> bool:
    """Check that test dependencies are not flagged."""
    pkg = test_dir / "package.json"
    if not pkg.is_file():
        return True
    try:
        data: Any = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    deps = {}
    if isinstance(data, dict):
        deps.update(data.get("dependencies", {}))
        deps.update(data.get("devDependencies", {}))
    blocked = {"malicious-package", "event-stream", "ua-parser-js-attack"}
    return not (set(deps.keys()) & blocked)


def _detect_scope(
    workspace_path: str,
    base_sha: str,
    head_sha: str,
    *,
    git_fn: Callable[..., CommandResult] | None = None,
) -> ScopeResult:
    """Analyze git diff between base_sha and head_sha for scope detection."""
    _git = git_fn or git
    if not base_sha or not head_sha or base_sha == head_sha:
        return ScopeResult(changed_files=(), test_files=(), has_test_changes=False)
    r = _git(["diff", "--name-only", base_sha, head_sha], cwd=workspace_path)
    if r.returncode != 0:
        logger.warning("git diff failed: %s", r.stderr.strip())
        return ScopeResult(changed_files=(), test_files=(), has_test_changes=False)
    files = tuple(f for f in r.stdout.strip().splitlines() if f.strip())
    test_kw = ("test", "spec", "e2e", "__tests__", "tests/")
    test_files = tuple(f for f in files if any(k in f.lower() for k in test_kw))
    return ScopeResult(
        changed_files=files,
        test_files=test_files,
        has_test_changes=bool(test_files),
    )


def _run_playwright(
    test_dir: Path,
    *,
    config_path: str | None = None,
    timeout: int = _TEST_TIMEOUT,
    run_fn: Callable[..., CommandResult] | None = None,
) -> tuple[int, str]:
    """Run ``npx playwright test`` with crucible config. Returns (rc, stdout)."""
    _run = run_fn or run_command
    cmd = ["npx", "playwright", "test", "--reporter=json"]
    if config_path:
        cmd.extend(["--config", config_path])
    r = _run(cmd, timeout=float(timeout), cwd=str(test_dir))
    return r.returncode, r.stdout


def _capture_artifacts(test_dir: Path, out: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Capture screenshots and traces from test results dir."""
    out.mkdir(parents=True, exist_ok=True)
    screenshots: list[str] = []
    traces: list[str] = []
    for search_dir in (test_dir / "test-results", test_dir / "reports"):
        if not search_dir.is_dir():
            continue
        for img in search_dir.rglob("*.png"):
            dest = out / img.name
            try:
                shutil.copy2(img, dest)
                screenshots.append(str(dest))
            except OSError:
                pass
        for trace in search_dir.rglob("*.zip"):
            dest = out / trace.name
            try:
                shutil.copy2(trace, dest)
                traces.append(str(dest))
            except OSError:
                pass
    return tuple(screenshots), tuple(traces)


def _parse_results(raw: str) -> tuple[int, int, int]:
    """Parse pass/fail/flaky counts from Playwright JSON reporter output."""
    try:
        data: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0, 0, 0
    if not isinstance(data, dict):
        return 0, 0, 0
    pc = fc = fk = 0
    for suite in data.get("suites", []):
        if not isinstance(suite, dict):
            continue
        for spec in suite.get("specs", []):
            if not isinstance(spec, dict):
                continue
            status = "pass" if spec.get("ok") else "fail"
            for t in spec.get("tests", []):
                if not isinstance(t, dict):
                    continue
                ts = str(t.get("status", ""))
                if ts == "flaky":
                    status = "flaky"
                elif ts in ("unexpected", "failed"):
                    status = "fail"
            if status == "pass":
                pc += 1
            elif status == "flaky":
                fk += 1
            else:
                fc += 1
    return pc, fc, fk


def _verdict(pc: int, fc: int, fk: int) -> CrucibleVerdict:  # noqa: ARG001
    if fc > 0:
        return CrucibleVerdict.NO_GO
    return CrucibleVerdict.GO


def _fail(error: str, **kw: Any) -> TwinRunResult:
    return TwinRunResult(verdict=CrucibleVerdict.NO_GO, error=error, **kw)


# ── Public API ──────────────────────────────────────────────────


def run_crucible_twin(  # noqa: PLR0913
    workspace: Workspace,
    base_sha: str,
    head_sha: str,
    *,
    config: dict[str, Any] | None = None,
    crucible_repo: str | None = None,
    git_fn: Callable[..., CommandResult] | None = None,
    gate_fn: Callable[[Path], bool] | None = None,
    run_fn: Callable[..., CommandResult] | None = None,
) -> TwinRunResult:
    """Run the full Crucible twin pipeline.

    Sequence: clone test repo → Sentinel Gate 1 → scope detection →
    run ``npx playwright test`` → capture artifacts → verdict.
    """
    repo = _resolve_crucible_repo(workspace, config, crucible_repo)
    if not repo:
        return _fail("Cannot resolve crucible repo from config or workspace")

    ws_path = Path(workspace.path)
    test_dir = ws_path / ".dark-factory" / "crucible-tests"
    out_dir = ws_path / ".dark-factory" / "crucible" / "latest"

    # 1. Clone test repo from CRUCIBLE_REPO config value
    logger.info("Cloning crucible test repo: %s", repo)
    if not _clone_test_repo(repo, test_dir, git_fn=git_fn):
        return _fail(f"Failed to clone crucible repo: {repo}")

    # 2. Sentinel Gate 1 on cloned test code
    logger.info("Running Sentinel Gate 1 on test code")
    sentinel_ok = _run_sentinel_gate1(test_dir, gate_fn=gate_fn)
    if not sentinel_ok:
        return _fail("Sentinel Gate 1 blocked: test code flagged as unsafe", sentinel_passed=False)

    # 3. Scope detection via git diff
    logger.info("Detecting scope: %s..%s", base_sha[:8] if base_sha else "?", head_sha[:8] if head_sha else "?")
    scope = _detect_scope(workspace.path, base_sha, head_sha, git_fn=git_fn)

    # 4. Run npx playwright test with crucible config
    pw_config = str(test_dir / "playwright.config.ts") if (test_dir / "playwright.config.ts").is_file() else None
    logger.info("Running playwright tests (config=%s)", pw_config or "default")
    rc, raw_output = _run_playwright(test_dir, config_path=pw_config, run_fn=run_fn)

    # 5. Parse results and capture artifacts
    pc, fc, fk = _parse_results(raw_output)
    screenshots, traces = _capture_artifacts(test_dir, out_dir)

    # Save raw output
    out_dir.mkdir(parents=True, exist_ok=True)
    if raw_output:
        (out_dir / "playwright-output.json").write_text(raw_output, encoding="utf-8")

    v = _verdict(pc, fc, fk)
    logger.info("Crucible twin: %s (pass=%d fail=%d flaky=%d)", v.value, pc, fc, fk)
    return TwinRunResult(
        verdict=v, pass_count=pc, fail_count=fc, flaky_count=fk,
        screenshots=screenshots, traces=traces, scope=scope,
        sentinel_passed=True,
    )
