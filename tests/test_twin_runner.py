"""Tests for factory.crucible.twin_runner — Crucible twin infrastructure wiring."""
from __future__ import annotations

import json
from pathlib import Path

from factory.crucible.orchestrator import CrucibleVerdict
from factory.crucible.twin_runner import (
    _check_deps_safe,
    _check_no_secrets,
    _detect_scope,
    _parse_results,
    _resolve_crucible_repo,
    _verdict,
    run_crucible_twin,
)
from factory.integrations.shell import CommandResult
from factory.workspace.manager import Workspace

# ── Helpers ──────────────────────────────────────────────────────


def _cr(stdout: str = "", stderr: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(stdout=stdout, stderr=stderr, returncode=returncode, duration_ms=0.0)


def _workspace(
    name: str = "owner/repo",
    path: str = "/tmp/ws",
    branch: str = "dark-factory/issue-42",
) -> Workspace:
    return Workspace(
        name=name, path=path,
        repo_url=f"https://github.com/{name}.git",
        branch=branch,
    )


# ── Test _resolve_crucible_repo ──────────────────────────────────


def test_resolve_explicit_repo() -> None:
    ws = _workspace()
    result = _resolve_crucible_repo(ws, None, "custom/crucible-tests")
    assert result == "custom/crucible-tests"


def test_resolve_from_config_crucible_repo() -> None:
    ws = _workspace()
    cfg = {"crucible_repo": "org/my-tests"}
    result = _resolve_crucible_repo(ws, cfg, None)
    assert result == "org/my-tests"


def test_resolve_from_config_uppercase() -> None:
    ws = _workspace()
    cfg = {"CRUCIBLE_REPO": "org/tests-upper"}
    result = _resolve_crucible_repo(ws, cfg, None)
    assert result == "org/tests-upper"


def test_resolve_from_workspace_name() -> None:
    ws = _workspace(name="myorg/myapp")
    result = _resolve_crucible_repo(ws, None, None)
    assert result == "myorg/myapp-crucible"


def test_resolve_invalid_workspace_name() -> None:
    ws = _workspace(name="no-slash")
    result = _resolve_crucible_repo(ws, None, None)
    assert result == ""


# ── Test _detect_scope ───────────────────────────────────────────


def test_detect_scope_with_changes() -> None:
    diff_output = "src/main.ts\nsrc/utils.ts\ntests/main.spec.ts\n"

    def fake_git(args: list[str], **kw: object) -> CommandResult:
        return _cr(stdout=diff_output)

    result = _detect_scope("/tmp/ws", "abc123", "def456", git_fn=fake_git)
    assert len(result.changed_files) == 3
    assert len(result.test_files) == 1
    assert result.has_test_changes is True


def test_detect_scope_no_test_changes() -> None:
    diff_output = "src/main.ts\nsrc/utils.ts\n"

    def fake_git(args: list[str], **kw: object) -> CommandResult:
        return _cr(stdout=diff_output)

    result = _detect_scope("/tmp/ws", "abc123", "def456", git_fn=fake_git)
    assert len(result.changed_files) == 2
    assert len(result.test_files) == 0
    assert result.has_test_changes is False


def test_detect_scope_same_sha() -> None:
    result = _detect_scope("/tmp/ws", "abc123", "abc123")
    assert result.changed_files == ()
    assert result.has_test_changes is False


def test_detect_scope_empty_sha() -> None:
    result = _detect_scope("/tmp/ws", "", "")
    assert result.changed_files == ()


def test_detect_scope_git_failure() -> None:
    def fake_git(args: list[str], **kw: object) -> CommandResult:
        return _cr(returncode=1, stderr="fatal: bad revision")

    result = _detect_scope("/tmp/ws", "abc", "def", git_fn=fake_git)
    assert result.changed_files == ()


# ── Test _parse_results ──────────────────────────────────────────


def test_parse_results_mixed() -> None:
    data = {
        "suites": [
            {
                "specs": [
                    {"title": "test1", "ok": True, "tests": [{"status": "expected", "results": []}]},
                    {"title": "test2", "ok": False, "tests": [{"status": "unexpected", "results": []}]},
                    {"title": "test3", "ok": True, "tests": [{"status": "flaky", "results": []}]},
                ],
            },
        ],
    }
    pc, fc, fk = _parse_results(json.dumps(data))
    assert pc == 1
    assert fc == 1
    assert fk == 1


def test_parse_results_all_pass() -> None:
    data = {
        "suites": [
            {"specs": [
                {"title": "t1", "ok": True, "tests": [{"status": "expected"}]},
                {"title": "t2", "ok": True, "tests": [{"status": "expected"}]},
            ]},
        ],
    }
    pc, fc, fk = _parse_results(json.dumps(data))
    assert pc == 2
    assert fc == 0
    assert fk == 0


def test_parse_results_invalid_json() -> None:
    pc, fc, fk = _parse_results("not json")
    assert (pc, fc, fk) == (0, 0, 0)


def test_parse_results_empty_suites() -> None:
    pc, fc, fk = _parse_results(json.dumps({"suites": []}))
    assert (pc, fc, fk) == (0, 0, 0)


# ── Test _verdict ────────────────────────────────────────────────


def test_verdict_all_pass() -> None:
    assert _verdict(5, 0, 0) == CrucibleVerdict.GO


def test_verdict_failures() -> None:
    assert _verdict(3, 2, 0) == CrucibleVerdict.NO_GO


def test_verdict_flaky_only() -> None:
    assert _verdict(3, 0, 2) == CrucibleVerdict.GO


# ── Test _check_deps_safe ───────────────────────────────────────


def test_check_deps_safe_clean(tmp_path: Path) -> None:
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"devDependencies": {"@playwright/test": "^1.40"}}))
    assert _check_deps_safe(tmp_path) is True


def test_check_deps_safe_blocked(tmp_path: Path) -> None:
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"dependencies": {"malicious-package": "1.0"}}))
    assert _check_deps_safe(tmp_path) is False


def test_check_deps_safe_no_package(tmp_path: Path) -> None:
    assert _check_deps_safe(tmp_path) is True


# ── Test _check_no_secrets ───────────────────────────────────────


def test_check_no_secrets_clean(tmp_path: Path) -> None:
    (tmp_path / "test.ts").write_text("const x = process.env.API_KEY;")
    assert _check_no_secrets(tmp_path) is True


def test_check_no_secrets_env_reference(tmp_path: Path) -> None:
    """Secrets referenced via process.env are ok."""
    (tmp_path / "test.ts").write_text("const key = process.env.api_key;")
    assert _check_no_secrets(tmp_path) is True


# ── Test run_crucible_twin full pipeline ─────────────────────────


def test_run_crucible_twin_success(tmp_path: Path) -> None:
    ws_path = tmp_path / "workspace"
    ws_path.mkdir()
    ws = Workspace(name="org/app", path=str(ws_path), repo_url="https://github.com/org/app.git", branch="test")

    # Pre-create the test dir with .git so clone "succeeds"
    test_dir = ws_path / ".dark-factory" / "crucible-tests"
    test_dir.mkdir(parents=True)
    (test_dir / ".git").mkdir()
    (test_dir / "playwright.config.ts").write_text("export default {};")

    results_json = json.dumps({
        "suites": [{"specs": [
            {"title": "t1", "ok": True, "tests": [{"status": "expected"}]},
            {"title": "t2", "ok": True, "tests": [{"status": "expected"}]},
        ]}],
    })

    def fake_git(args: list[str], **kw: object) -> CommandResult:
        if "diff" in args:
            return _cr(stdout="src/main.ts\n")
        return _cr()

    def fake_run(cmd: list[str], **kw: object) -> CommandResult:
        return _cr(stdout=results_json)

    result = run_crucible_twin(
        ws, "abc123", "def456",
        crucible_repo="org/app-crucible",
        git_fn=fake_git,
        gate_fn=lambda _: True,
        run_fn=fake_run,
    )
    assert result.verdict == CrucibleVerdict.GO
    assert result.pass_count == 2
    assert result.fail_count == 0
    assert result.sentinel_passed is True
    assert result.scope is not None
    assert result.scope.has_test_changes is False


def test_run_crucible_twin_no_repo() -> None:
    ws = _workspace(name="bad-name")
    result = run_crucible_twin(ws, "a", "b")
    assert result.verdict == CrucibleVerdict.NO_GO
    assert "Cannot resolve" in result.error


def test_run_crucible_twin_clone_failure(tmp_path: Path) -> None:
    ws = Workspace(name="org/app", path=str(tmp_path), repo_url="", branch="test")

    def fake_git(args: list[str], **kw: object) -> CommandResult:
        return _cr(returncode=1, stderr="clone failed")

    result = run_crucible_twin(
        ws, "a", "b",
        crucible_repo="org/tests",
        git_fn=fake_git,
    )
    assert result.verdict == CrucibleVerdict.NO_GO
    assert "Failed to clone" in result.error


def test_run_crucible_twin_sentinel_blocks(tmp_path: Path) -> None:
    ws_path = tmp_path / "workspace"
    ws_path.mkdir()
    ws = Workspace(name="org/app", path=str(ws_path), repo_url="", branch="test")

    # Pre-create test dir
    test_dir = ws_path / ".dark-factory" / "crucible-tests"
    test_dir.mkdir(parents=True)
    (test_dir / ".git").mkdir()

    def fake_git(args: list[str], **kw: object) -> CommandResult:
        return _cr()

    result = run_crucible_twin(
        ws, "a", "b",
        crucible_repo="org/tests",
        git_fn=fake_git,
        gate_fn=lambda _: False,
    )
    assert result.verdict == CrucibleVerdict.NO_GO
    assert result.sentinel_passed is False
    assert "Sentinel Gate 1" in result.error


def test_run_crucible_twin_test_failures(tmp_path: Path) -> None:
    ws_path = tmp_path / "workspace"
    ws_path.mkdir()
    ws = Workspace(name="org/app", path=str(ws_path), repo_url="", branch="test")

    test_dir = ws_path / ".dark-factory" / "crucible-tests"
    test_dir.mkdir(parents=True)
    (test_dir / ".git").mkdir()

    results_json = json.dumps({
        "suites": [{"specs": [
            {"title": "t1", "ok": True, "tests": [{"status": "expected"}]},
            {"title": "t2", "ok": False, "tests": [{"status": "unexpected"}]},
        ]}],
    })

    def fake_git(args: list[str], **kw: object) -> CommandResult:
        if "diff" in args:
            return _cr(stdout="")
        return _cr()

    def fake_run(cmd: list[str], **kw: object) -> CommandResult:
        return _cr(stdout=results_json, returncode=1)

    result = run_crucible_twin(
        ws, "a", "b",
        crucible_repo="org/tests",
        git_fn=fake_git,
        gate_fn=lambda _: True,
        run_fn=fake_run,
    )
    assert result.verdict == CrucibleVerdict.NO_GO
    assert result.pass_count == 1
    assert result.fail_count == 1
