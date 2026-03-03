"""Crucible test orchestrator — build, test, capture, teardown."""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.integrations.shell import CommandResult
    from dark_factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_BUILD_T, _HEALTH_T, _TEST_T, _TD_T = 300, 60, 600, 30
_POLL_S = 2.0


class CrucibleVerdict(Enum):
    GO = "GO"
    NO_GO = "NO_GO"
    NEEDS_LIVE = "NEEDS_LIVE"


@dataclass(frozen=True, slots=True)
class TestResult:
    name: str
    status: str
    duration_ms: float = 0.0

@dataclass(frozen=True, slots=True)
class PhaseMetrics:
    phase: str
    duration_s: float
    passed: bool
    detail: str = ""

@dataclass(frozen=True, slots=True)
class CrucibleResult:
    """Full outcome of a Crucible test run."""
    verdict: CrucibleVerdict
    test_results: tuple[TestResult, ...]
    screenshots: tuple[str, ...]
    logs: str
    phases: tuple[PhaseMetrics, ...]
    pass_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    duration_s: float = 0.0
    error: str = ""

@dataclass(frozen=True, slots=True)
class CrucibleConfig:
    """Per-phase timeouts and runtime settings."""
    build_timeout: int = _BUILD_T
    health_timeout: int = _HEALTH_T
    test_timeout: int = _TEST_T
    compose_file: str = ""
    project_name: str = "crucible"
    docker_fn: Callable[..., CommandResult] | None = None
    repo: str = ""
    num_shards: int = 1

def _dk(args: list[str], **kw: Any) -> CommandResult:  # noqa: ANN401
    from dark_factory.integrations.shell import docker  # noqa: PLC0415
    return docker(args, **kw)

def _timed(phase: str, fn: Callable[[], bool], timeout: int) -> PhaseMetrics:
    t0 = time.monotonic()
    try:
        ok = fn()
    except Exception as exc:  # noqa: BLE001
        return PhaseMetrics(phase=phase, duration_s=time.monotonic() - t0, passed=False, detail=str(exc))
    el = time.monotonic() - t0
    if el > timeout:
        return PhaseMetrics(phase=phase, duration_s=el, passed=False, detail="timeout")
    return PhaseMetrics(phase=phase, duration_s=el, passed=ok)

def _cf(ws: Workspace, cfg: CrucibleConfig) -> str:
    """Return compose file path relative to ws.path (Docker runs with cwd=ws.path)."""
    if cfg.compose_file:
        return cfg.compose_file
    # Check absolute path for existence, but return relative for Docker -f
    abs_d = Path(ws.path) / ".dark-factory" / "generated"
    rel_d = Path(".dark-factory") / "generated"
    for n in ("docker-compose.crucible.yml", "docker-compose.yml"):
        if (abs_d / n).is_file():
            return str(rel_d / n)
    return str(rel_d / "docker-compose.yml")

def _dc(cfg: CrucibleConfig, ws: Workspace | None = None) -> list[str]:
    b = ["compose", "-p", cfg.project_name]
    if ws:
        b.extend(["-f", _cf(ws, cfg)])
    return b

def _build(ws: Workspace, cfg: CrucibleConfig) -> bool:
    r = (cfg.docker_fn or _dk)(_dc(cfg, ws) + ["build"], timeout=float(cfg.build_timeout), cwd=ws.path)
    if r.returncode != 0:
        logger.error("Build failed: %s", r.stderr.strip())
    return r.returncode == 0

def _up(ws: Workspace, cfg: CrucibleConfig) -> bool:
    r = (cfg.docker_fn or _dk)(_dc(cfg, ws) + ["up", "-d"], timeout=float(cfg.build_timeout), cwd=ws.path)
    if r.returncode != 0:
        logger.error("Compose up failed: %s", r.stderr.strip())
    return r.returncode == 0

def _health(cfg: CrucibleConfig) -> bool:
    fn = cfg.docker_fn or _dk
    deadline = time.monotonic() + cfg.health_timeout
    while time.monotonic() < deadline:
        r = fn(["compose", "-p", cfg.project_name, "ps", "--format", "json"], timeout=10.0)
        if r.returncode == 0 and r.stdout.strip():
            try:
                svcs = _parse_ps(r.stdout)
                if svcs and all(s.get("Health") == "healthy" or s.get("State") == "running" for s in svcs):
                    return True
            except (json.JSONDecodeError, TypeError):
                pass
        time.sleep(_POLL_S)
    logger.error("Health check timed out after %ds", cfg.health_timeout)
    return False

def _parse_ps(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if raw.startswith("["):
        r: Any = json.loads(raw)
        return list(r) if isinstance(r, list) else []
    out: list[dict[str, Any]] = []
    for ln in raw.splitlines():
        if ln.strip():
            p: Any = json.loads(ln.strip())
            if isinstance(p, dict):
                out.append(p)
    return out

def _exec_tests(
    ws: Workspace,
    cfg: CrucibleConfig,
    *,
    run_cmd: str = "",
    test_files: list[str] | None = None,
    container: str = "",
) -> tuple[int, str]:
    """Execute tests inside the container.

    Args:
        ws: Application workspace.
        cfg: Crucible configuration.
        run_cmd: Override test command (default: ``npx playwright test --reporter=json``).
        test_files: Specific test files to run (appended to command).
        container: Override container name (default: ``{project}-app-1``).
    """
    ctr = container or f"{cfg.project_name}-app-1"
    cmd = run_cmd or "npx playwright test --reporter=json"
    if test_files:
        cmd = f"{cmd} {' '.join(test_files)}"
    r = (cfg.docker_fn or _dk)(["exec", ctr, "sh", "-c",
         f"cd /workspace && {cmd} 2>&1 || true"],
         timeout=float(cfg.test_timeout), cwd=ws.path)
    return r.returncode, r.stdout

def _capture(ws: Workspace, cfg: CrucibleConfig, out: Path) -> tuple[str, tuple[str, ...]]:
    r = (cfg.docker_fn or _dk)(["compose", "-p", cfg.project_name, "logs", "--tail", "200"],
                                timeout=float(_TD_T), cwd=ws.path)
    logs = r.stdout if r.returncode == 0 else ""
    if logs:
        (out / "container-logs.txt").write_text(logs, encoding="utf-8")
    shots: list[str] = []
    tr = Path(ws.path) / "test-results"
    if tr.is_dir():
        for pat in ("**/*.png", "**/*.jpg"):
            for img in tr.glob(pat):
                dest = out / img.name
                try:
                    shutil.copy2(img, dest)
                    shots.append(str(dest))
                except OSError:
                    pass
    return logs, tuple(shots)

def _down(cfg: CrucibleConfig, ws: Workspace) -> bool:
    r = (cfg.docker_fn or _dk)(_dc(cfg, ws) + ["down", "-v", "--remove-orphans"],
                                timeout=float(_TD_T), cwd=ws.path)
    return r.returncode == 0

def _parse_tests(raw: str) -> tuple[list[TestResult], int, int, int]:
    results: list[TestResult] = []
    pc = fc = sc = 0
    try:
        data: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return results, pc, fc, sc
    for suite in (data.get("suites", []) if isinstance(data, dict) else []):
        if not isinstance(suite, dict):
            continue
        for spec in suite.get("specs", []):
            if not isinstance(spec, dict):
                continue
            name, status, dur = str(spec.get("title", "unknown")), ("pass" if spec.get("ok") else "fail"), 0.0
            for t in spec.get("tests", []):
                if not isinstance(t, dict):
                    continue
                rl = t.get("results", [])
                dur += float(rl[0].get("duration", 0)) if rl else 0.0
                st = str(t.get("status", ""))
                if st == "skipped":
                    status = "skip"
                elif st in ("unexpected", "flaky"):
                    status = "fail"
            results.append(TestResult(name=name, status=status, duration_ms=dur))
            if status == "pass":
                pc += 1
            elif status == "fail":
                fc += 1
            else:
                sc += 1
    return results, pc, fc, sc

def _verdict(pc: int, fc: int, sc: int) -> CrucibleVerdict:
    if fc > 0:
        return CrucibleVerdict.NO_GO
    return CrucibleVerdict.NEEDS_LIVE if sc > 0 else CrucibleVerdict.GO

def _save(result: CrucibleResult, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    summary = {"verdict": result.verdict.value, "pass": result.pass_count,
               "fail": result.fail_count, "skip": result.skip_count,
               "duration_s": round(result.duration_s, 2),
               "phases": [{"phase": p.phase, "duration_s": round(p.duration_s, 2),
                            "passed": p.passed, "detail": p.detail} for p in result.phases]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if result.test_results:
        lines = [json.dumps({"name": t.name, "status": t.status, "duration_ms": t.duration_ms})
                 for t in result.test_results]
        (out / "details.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if result.error:
        (out / "error.txt").write_text(result.error, encoding="utf-8")

def _fail(phases: list[PhaseMetrics], ws: Workspace, cfg: CrucibleConfig,  # noqa: PLR0913
          out: Path, t0: float, error: str, teardown: bool = False) -> CrucibleResult:
    logs, shots = _capture(ws, cfg, out)
    if teardown:
        _down(cfg, ws)
    r = CrucibleResult(verdict=CrucibleVerdict.NO_GO, test_results=(), screenshots=shots,
                        logs=logs, phases=tuple(phases), duration_s=time.monotonic() - t0, error=error)
    _save(r, out)
    return r

# ── Public API ──────────────────────────────────────────────────


def _provision_repo(repo: str, workspace_path: str) -> PhaseMetrics:
    """Sync the Crucible test repo locally before running tests."""
    from dark_factory.crucible.repo_provision import manage_crucible_repo  # noqa: PLC0415

    t0 = time.monotonic()
    try:
        result = manage_crucible_repo(repo, target_dir=Path(workspace_path) / ".dark-factory" / "crucible-tests")
        ok = not result.error
        detail = result.error if result.error else f"synced {result.crucible_repo}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, str(exc)
    return PhaseMetrics(phase="repo-provision", duration_s=time.monotonic() - t0, passed=ok, detail=detail)


def run_crucible(workspace: Workspace, config: CrucibleConfig | None = None, *,  # noqa: PLR0912
                 issue_number: int = 0) -> CrucibleResult:
    """Run the full Crucible test sequence.

    Sequence: provision test repo (if configured) -> build container ->
    start compose -> health check -> run tests -> capture results -> teardown.
    Results saved to ``.dark-factory/crucible/{issue_number}/``.
    """
    cfg = config or CrucibleConfig()
    out = Path(workspace.path) / ".dark-factory" / "crucible" / str(issue_number or "latest")
    out.mkdir(parents=True, exist_ok=True)
    phases: list[PhaseMetrics] = []
    t0 = time.monotonic()
    # 0. Provision crucible test repo (if repo is configured)
    if cfg.repo:
        pm = _provision_repo(cfg.repo, workspace.path)
        phases.append(pm)
        if not pm.passed:
            logger.warning("Crucible repo provision failed: %s (continuing anyway)", pm.detail)
    # 1. Build
    pm = _timed("build", lambda: _build(workspace, cfg), cfg.build_timeout)
    phases.append(pm)
    if not pm.passed:
        return _fail(phases, workspace, cfg, out, t0, f"Build failed: {pm.detail}")
    # 2. Compose up
    pm = _timed("compose", lambda: _up(workspace, cfg), cfg.build_timeout)
    phases.append(pm)
    if not pm.passed:
        return _fail(phases, workspace, cfg, out, t0, f"Compose up failed: {pm.detail}", teardown=True)
    # 3. Health check
    pm = _timed("health", lambda: _health(cfg), cfg.health_timeout)
    phases.append(pm)
    if not pm.passed:
        return _fail(phases, workspace, cfg, out, t0, f"Health check failed: {pm.detail}", teardown=True)
    # 4. Run tests
    test_out = ""
    def _t() -> bool:  # noqa: E301
        nonlocal test_out
        _, test_out = _exec_tests(workspace, cfg)
        return True
    pm = _timed("tests", _t, cfg.test_timeout)
    phases.append(pm)
    # 5. Parse + verdict
    trs, pc, fc, sc = _parse_tests(test_out)
    if not trs and pm.detail == "timeout":
        fc = 1
    v = _verdict(pc, fc, sc)
    # 6. Capture on failure
    logs: str = ""
    shots: tuple[str, ...] = ()
    if v != CrucibleVerdict.GO:
        logs, shots = _capture(workspace, cfg, out)
        if test_out:
            (out / "test-output.txt").write_text(test_out, encoding="utf-8")
    # 7. Teardown
    phases.append(_timed("teardown", lambda: _down(cfg, workspace), _TD_T))
    result = CrucibleResult(verdict=v, test_results=tuple(trs), screenshots=shots, logs=logs,
                             phases=tuple(phases), pass_count=pc, fail_count=fc,
                             skip_count=sc, duration_s=time.monotonic() - t0)
    _save(result, out)
    logger.info("Crucible: %s (pass=%d fail=%d skip=%d %.1fs)", v.value, pc, fc, sc, result.duration_s)
    return result


def run_sharded_crucible(
    workspace: Workspace,
    config: CrucibleConfig | None = None,
    *,
    issue_number: int = 0,
    test_dir: str = "tests",
    durations: dict[str, float] | None = None,
) -> CrucibleResult:
    """Run Crucible with optional test sharding.

    When ``config.num_shards`` is 1 (the default), delegates directly to
    :func:`run_crucible`.  When > 1, partitions test files across shards,
    runs each sequentially, and merges verdicts.
    """
    from dark_factory.crucible.sharding import ShardResult, merge_verdicts, partition_tests  # noqa: PLC0415

    cfg = config or CrucibleConfig()
    if cfg.num_shards <= 1:
        return run_crucible(workspace, cfg, issue_number=issue_number)

    # Discover test files
    td = Path(workspace.path) / test_dir
    test_files = sorted(td.rglob("*.spec.*")) + sorted(td.rglob("*.test.*"))
    if not test_files:
        test_files = sorted(td.rglob("*.ts")) + sorted(td.rglob("*.js"))

    if len(test_files) <= cfg.num_shards:
        return run_crucible(workspace, cfg, issue_number=issue_number)

    shards = partition_tests(test_files, cfg.num_shards, durations=durations)
    shard_results: list[ShardResult] = []
    total_pass = total_fail = total_skip = 0
    all_phases: list[PhaseMetrics] = []
    t0 = time.monotonic()

    for idx, shard_files in enumerate(shards):
        if not shard_files:
            continue
        logger.info("Crucible shard %d/%d: %d test files", idx + 1, cfg.num_shards, len(shard_files))
        result = run_crucible(workspace, cfg, issue_number=issue_number)
        shard_results.append(ShardResult(shard_index=idx, result=result))
        total_pass += result.pass_count
        total_fail += result.fail_count
        total_skip += result.skip_count
        all_phases.extend(result.phases)

    merged_verdict = merge_verdicts(shard_results)
    last = shard_results[-1].result if shard_results else None
    return CrucibleResult(
        verdict=merged_verdict,
        test_results=last.test_results if last else (),
        screenshots=last.screenshots if last else (),
        logs=last.logs if last else "",
        phases=tuple(all_phases),
        pass_count=total_pass,
        fail_count=total_fail,
        skip_count=total_skip,
        duration_s=time.monotonic() - t0,
    )
