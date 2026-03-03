"""Tests for the Foundry workspace onboarding flow (US-009).

Covers:
- Repo URL parsing (HTTPS, SSH, owner/repo, invalid)
- OnboardConfig DI pattern
- Full onboarding flow: clone → webhooks → strategy → scan mode → gate 1
- Config persistence to .dark-factory/config.json
- Workspace appears in Foundry workspace list after onboarding
- Failure paths (clone fail, invalid URL, config save)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dark_factory.modes.foundry_onboard import (
    OnboardConfig,
    OnboardResult,
    OnboardStep,
    WorkspaceEntry,
    build_clone_url,
    load_workspace_configs,
    parse_repo_from_url,
    run_onboard_workspace,
    save_workspace_config,
)

# ── Repo URL parsing ──────────────────────────────────────────────


class TestParseRepoFromUrl:
    def test_valid_url_formats(self) -> None:
        """All valid URL formats parse correctly to owner/repo."""
        cases = {
            "https://github.com/acme/web-app": "acme/web-app",
            "https://github.com/acme/web-app.git": "acme/web-app",
            "git@github.com:acme/web-app.git": "acme/web-app",
            "acme/web-app": "acme/web-app",
            "  acme/repo  ": "acme/repo",
            "https://github.com/acme/web-app/": "acme/web-app",
        }
        for url, expected in cases.items():
            assert parse_repo_from_url(url) == expected, f"Failed for URL: {url!r}"

    def test_invalid_urls_raise(self) -> None:
        """Invalid and empty URLs raise ValueError."""
        for url in ("not-a-url", ""):
            with pytest.raises(ValueError, match="Cannot parse repo"):
                parse_repo_from_url(url)


def test_build_clone_url() -> None:
    assert build_clone_url("acme/web-app") == "https://github.com/acme/web-app.git"


# ── OnboardStep / OnboardResult models ───────────────────────────


def test_onboard_step_is_frozen() -> None:
    step = OnboardStep(name="test", passed=True, message="ok")
    with pytest.raises(AttributeError):
        step.name = "changed"  # type: ignore[misc]


def test_onboard_result_fields() -> None:
    result = OnboardResult(
        success=True, repo="acme/app", strategy="web",
        scan_mode="full", steps=(),
    )
    assert result.success is True
    assert result.repo == "acme/app"
    assert result.strategy == "web"
    assert result.scan_mode == "full"


# ── WorkspaceEntry model ─────────────────────────────────────────


def test_workspace_entry_defaults() -> None:
    entry = WorkspaceEntry(repo="acme/app", strategy="web", scan_mode="full")
    assert entry.status == "active"
    assert entry.webhook_status == "enabled"
    assert entry.watched_branch == "main"


def test_workspace_entry_is_frozen() -> None:
    entry = WorkspaceEntry(repo="acme/app", strategy="web", scan_mode="full")
    with pytest.raises(AttributeError):
        entry.repo = "changed"  # type: ignore[misc]


# ── Config persistence ────────────────────────────────────────────


class TestConfigPersistence:
    def test_save_and_load_workspace(self, tmp_path: Path) -> None:
        entry = WorkspaceEntry(
            repo="acme/web-app", strategy="web", scan_mode="full",
        )
        save_workspace_config(entry, config_root=tmp_path)

        loaded = load_workspace_configs(config_root=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].repo == "acme/web-app"
        assert loaded[0].strategy == "web"
        assert loaded[0].scan_mode == "full"
        assert loaded[0].status == "active"

    def test_save_creates_config_dir(self, tmp_path: Path) -> None:
        entry = WorkspaceEntry(repo="x/y", strategy="console", scan_mode="fast")
        path = save_workspace_config(entry, config_root=tmp_path)
        assert path.is_file()
        assert (tmp_path / ".dark-factory").is_dir()

    def test_save_multiple_workspaces(self, tmp_path: Path) -> None:
        save_workspace_config(
            WorkspaceEntry(repo="a/b", strategy="web", scan_mode="full"),
            config_root=tmp_path,
        )
        save_workspace_config(
            WorkspaceEntry(repo="c/d", strategy="console", scan_mode="fast"),
            config_root=tmp_path,
        )
        loaded = load_workspace_configs(config_root=tmp_path)
        assert len(loaded) == 2
        repos = {e.repo for e in loaded}
        assert repos == {"a/b", "c/d"}

    def test_save_upserts_existing(self, tmp_path: Path) -> None:
        save_workspace_config(
            WorkspaceEntry(repo="a/b", strategy="web", scan_mode="full"),
            config_root=tmp_path,
        )
        save_workspace_config(
            WorkspaceEntry(repo="a/b", strategy="console", scan_mode="fast"),
            config_root=tmp_path,
        )
        loaded = load_workspace_configs(config_root=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].strategy == "console"

    def test_load_missing_config_returns_empty(self, tmp_path: Path) -> None:
        assert load_workspace_configs(config_root=tmp_path) == []

    def test_load_corrupted_config_returns_empty(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".dark-factory"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("not valid json", encoding="utf-8")
        assert load_workspace_configs(config_root=tmp_path) == []

    def test_load_preserves_extra_config_fields(self, tmp_path: Path) -> None:
        """Saving a workspace doesn't clobber other config keys."""
        config_dir = tmp_path / ".dark-factory"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            json.dumps({"version": "1.0", "repos": []}),
            encoding="utf-8",
        )
        save_workspace_config(
            WorkspaceEntry(repo="a/b", strategy="web", scan_mode="full"),
            config_root=tmp_path,
        )
        data = json.loads(
            (config_dir / "config.json").read_text(encoding="utf-8"),
        )
        assert data["version"] == "1.0"
        assert len(data["repos"]) == 1


# ── Full onboarding flow ─────────────────────────────────────────


class TestOnboardFlow:
    def _make_config(
        self,
        tmp_path: Path,
        *,
        clone_ok: bool = True,
        webhooks_ok: bool = True,
        gate1_ok: bool = True,
    ) -> OnboardConfig:
        ws_root = tmp_path / "workspaces"

        def _clone(url: str, dest: Path) -> bool:
            if clone_ok:
                dest.mkdir(parents=True, exist_ok=True)
                (dest / ".git").mkdir()  # fake git dir
            return clone_ok

        return OnboardConfig(
            clone_fn=_clone,
            wire_webhooks_fn=lambda _repo: webhooks_ok,
            run_gate1_fn=lambda _path: gate1_ok,
            workspace_root=ws_root,
            config_root=tmp_path,
        )

    def test_successful_onboard(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        result = run_onboard_workspace("acme/web-app", strategy="web", scan_mode="full", config=cfg)
        assert result.success is True
        assert result.repo == "acme/web-app"
        assert result.strategy == "web"
        assert result.scan_mode == "full"

    def test_all_steps_pass(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        result = run_onboard_workspace("acme/web-app", config=cfg)
        step_names = [s.name for s in result.steps]
        assert "parse_url" in step_names
        assert "clone" in step_names
        assert "wire_webhooks" in step_names
        assert "deploy_strategy" in step_names
        assert "sentinel_scan_mode" in step_names
        assert "gate1_baseline" in step_names
        assert "save_config" in step_names
        assert all(s.passed for s in result.steps)

    def test_clone_failure_aborts(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path, clone_ok=False)
        result = run_onboard_workspace("acme/app", config=cfg)
        assert result.success is False
        clone_step = next(s for s in result.steps if s.name == "clone")
        assert clone_step.passed is False
        # Should not reach later steps
        step_names = [s.name for s in result.steps]
        assert "gate1_baseline" not in step_names

    def test_invalid_url_aborts(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        result = run_onboard_workspace("not-a-url", config=cfg)
        assert result.success is False
        assert result.steps[0].name == "parse_url"
        assert result.steps[0].passed is False

    def test_webhook_failure_is_nonfatal(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path, webhooks_ok=False)
        result = run_onboard_workspace("acme/app", config=cfg)
        assert result.success is True
        webhook_step = next(s for s in result.steps if s.name == "wire_webhooks")
        assert webhook_step.passed is False

    def test_gate1_failure_is_nonfatal(self, tmp_path: Path) -> None:
        """Gate 1 baseline failure doesn't block onboarding."""
        cfg = self._make_config(tmp_path, gate1_ok=False)
        result = run_onboard_workspace("acme/app", config=cfg)
        assert result.success is True
        gate_step = next(s for s in result.steps if s.name == "gate1_baseline")
        assert gate_step.passed is False

    def test_config_saved_after_onboard(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        run_onboard_workspace("acme/web-app", strategy="web", scan_mode="fast", config=cfg)
        entries = load_workspace_configs(config_root=tmp_path)
        assert len(entries) == 1
        assert entries[0].repo == "acme/web-app"
        assert entries[0].strategy == "web"

    def test_invalid_strategy_defaults_to_console(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        result = run_onboard_workspace("acme/app", strategy="invalid", config=cfg)
        assert result.strategy == "console"

    def test_invalid_scan_mode_defaults_to_full(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        result = run_onboard_workspace("acme/app", scan_mode="invalid", config=cfg)
        assert result.scan_mode == "full"

    def test_https_url_works(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        result = run_onboard_workspace(
            "https://github.com/acme/web-app.git",
            config=cfg,
        )
        assert result.success is True
        assert result.repo == "acme/web-app"

    def test_ssh_url_works(self, tmp_path: Path) -> None:
        cfg = self._make_config(tmp_path)
        result = run_onboard_workspace(
            "git@github.com:acme/web-app.git",
            config=cfg,
        )
        assert result.success is True
        assert result.repo == "acme/web-app"


# ── Foundry workspace list integration ───────────────────────────


def _fake_clone(_url: str, dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    return True


class TestFoundryListIntegration:
    def test_workspace_appears_in_foundry_list(self, tmp_path: Path) -> None:
        """After onboarding, workspace appears in load_workspaces()."""
        from dark_factory.modes.foundry import load_workspaces

        cfg = OnboardConfig(
            clone_fn=_fake_clone,
            wire_webhooks_fn=lambda _repo: True,
            run_gate1_fn=lambda _path: True,
            workspace_root=tmp_path / "workspaces",
            config_root=tmp_path,
        )
        run_onboard_workspace("acme/web-app", strategy="web", config=cfg)
        workspaces = load_workspaces(config_root=tmp_path)
        assert len(workspaces) == 1
        assert workspaces[0].repo == "acme/web-app"
        assert workspaces[0].strategy == "web"
        assert workspaces[0].status == "active"

    def test_multiple_onboards_appear_in_list(self, tmp_path: Path) -> None:
        from dark_factory.modes.foundry import load_workspaces

        cfg = OnboardConfig(
            clone_fn=_fake_clone,
            wire_webhooks_fn=lambda _repo: True,
            run_gate1_fn=lambda _path: True,
            workspace_root=tmp_path / "workspaces",
            config_root=tmp_path,
        )
        run_onboard_workspace("acme/web-app", strategy="web", config=cfg)
        run_onboard_workspace("acme/cli-tool", strategy="console", config=cfg)
        workspaces = load_workspaces(config_root=tmp_path)
        assert len(workspaces) == 2
        repos = {ws.repo for ws in workspaces}
        assert repos == {"acme/web-app", "acme/cli-tool"}
