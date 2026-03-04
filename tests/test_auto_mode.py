"""Tests for factory.modes.auto — the auto-mode loop."""

from __future__ import annotations

from unittest.mock import patch

from dark_factory.integrations.gh_safe import IssueInfo
from dark_factory.modes.auto import (
    LABEL_NEEDS_LIVE,
    VERDICT_GO,
    VERDICT_NEEDS_LIVE,
    VERDICT_NO_GO,
    AutoModeConfig,
    AutoModeState,
    CrucibleOutcome,
    CycleOutcome,
    _install_signal_handlers,
    run_auto_mode,
    run_crucible_phase,
    run_cycle,
    run_dark_forge,
)
from dark_factory.workspace.manager import Workspace

# ── Helpers ───────────────────────────────────────────────────────────


def _issue(number: int = 42, title: str = "Test issue") -> IssueInfo:
    return IssueInfo(number=number, title=title, labels=("factory-task",), state="OPEN")


def _workspace(path: str = "/tmp/ws", branch: str = "dark-factory/issue-42") -> Workspace:
    return Workspace(name="test/repo", path=path, repo_url="https://github.com/test/repo.git", branch=branch)


def _crucible_outcome(
    verdict: str = VERDICT_GO,
    error: str = "",
) -> CrucibleOutcome:
    return CrucibleOutcome(verdict=verdict, error=error, duration_s=1.0)


def _config(**overrides: object) -> AutoModeConfig:
    """Build test config with all external deps stubbed out."""
    defaults: dict[str, object] = {
        "repo": "test/repo",
        "forge_fn": lambda issue, ws: True,
        "crucible_fn": lambda ws, n: _crucible_outcome(),
        "deploy_fn": lambda ws, n: True,
        "ouroboros_fn": lambda issue, outcome, detail: None,
        "acquire_workspace_fn": lambda repo, n: _workspace(),
        "sentinel_fn": lambda ws, phase: True,
        "sleep_fn": lambda _: None,
        "max_cycles": 1,
    }
    defaults.update(overrides)
    return AutoModeConfig(**defaults)  # type: ignore[arg-type]


# ── run_dark_forge ────────────────────────────────────────────────────


class TestRunDarkForge:
    def test_success(self) -> None:
        result = run_dark_forge(
            _issue(), "/tmp/ws",
            forge_fn=lambda i, w: True,
            sentinel_fn=lambda w, p: True,
        )
        assert result is True

    def test_forge_failure(self) -> None:
        result = run_dark_forge(
            _issue(), "/tmp/ws",
            forge_fn=lambda i, w: False,
            sentinel_fn=lambda w, p: True,
        )
        assert result is False

    def test_pre_sentinel_failure(self) -> None:
        def sentinel(ws: str, phase: str) -> bool:
            return phase != "forge-pre"

        result = run_dark_forge(
            _issue(), "/tmp/ws",
            forge_fn=lambda i, w: True,
            sentinel_fn=sentinel,
        )
        assert result is False

    def test_post_sentinel_failure(self) -> None:
        def sentinel(ws: str, phase: str) -> bool:
            return phase != "forge-post"

        result = run_dark_forge(
            _issue(), "/tmp/ws",
            forge_fn=lambda i, w: True,
            sentinel_fn=sentinel,
        )
        assert result is False

    def test_post_sentinel_skipped_on_forge_failure(self) -> None:
        """Post-forge sentinel should not run if forge itself failed."""
        sentinel_calls: list[str] = []

        def sentinel(ws: str, phase: str) -> bool:
            sentinel_calls.append(phase)
            return True

        run_dark_forge(
            _issue(), "/tmp/ws",
            forge_fn=lambda i, w: False,
            sentinel_fn=sentinel,
        )
        assert "forge-post" not in sentinel_calls


# ── run_crucible_phase ────────────────────────────────────────────────


class TestRunCruciblePhase:
    def test_go_verdict(self) -> None:
        result = run_crucible_phase(
            _workspace(), 42,
            crucible_fn=lambda ws, n: _crucible_outcome(VERDICT_GO),
            sentinel_fn=lambda w, p: True,
        )
        assert result.verdict == VERDICT_GO

    def test_no_go_verdict(self) -> None:
        result = run_crucible_phase(
            _workspace(), 42,
            crucible_fn=lambda ws, n: _crucible_outcome(VERDICT_NO_GO, error="tests failed"),
            sentinel_fn=lambda w, p: True,
        )
        assert result.verdict == VERDICT_NO_GO

    def test_needs_live_verdict(self) -> None:
        result = run_crucible_phase(
            _workspace(), 42,
            crucible_fn=lambda ws, n: _crucible_outcome(VERDICT_NEEDS_LIVE),
            sentinel_fn=lambda w, p: True,
        )
        assert result.verdict == VERDICT_NEEDS_LIVE

    def test_pre_sentinel_failure_returns_no_go(self) -> None:
        def sentinel(ws: str, phase: str) -> bool:
            return phase != "crucible-pre"

        result = run_crucible_phase(
            _workspace(), 42,
            crucible_fn=lambda ws, n: _crucible_outcome(VERDICT_GO),
            sentinel_fn=sentinel,
        )
        assert result.verdict == VERDICT_NO_GO

    def test_post_sentinel_failure_overrides_go(self) -> None:
        def sentinel(ws: str, phase: str) -> bool:
            return phase != "crucible-post"

        result = run_crucible_phase(
            _workspace(), 42,
            crucible_fn=lambda ws, n: _crucible_outcome(VERDICT_GO),
            sentinel_fn=sentinel,
        )
        assert result.verdict == VERDICT_NO_GO


# ── run_cycle ─────────────────────────────────────────────────────────


class TestRunCycle:
    def test_full_success_cycle(self) -> None:
        result = run_cycle(_issue(), config=_config())
        assert result.outcome == CycleOutcome.SUCCESS
        assert result.issue_number == 42
        assert result.forge_attempts == 1
        assert result.duration_s > 0

    def test_forge_failure_exhausts_retries(self) -> None:
        result = run_cycle(
            _issue(),
            config=_config(forge_fn=lambda i, w: False, max_forge_retries=1),
        )
        assert result.outcome == CycleOutcome.FORGE_FAILED
        assert result.forge_attempts == 2  # 1 initial + 1 retry

    def test_crucible_no_go_feeds_back_to_forge(self) -> None:
        """NO_GO from Crucible triggers one more forge attempt."""
        call_count = {"forge": 0, "crucible": 0}

        def forge(issue: IssueInfo, ws: str) -> bool:
            call_count["forge"] += 1
            return True

        def crucible(ws: Workspace, n: int) -> CrucibleOutcome:
            call_count["crucible"] += 1
            if call_count["crucible"] == 1:
                return _crucible_outcome(VERDICT_NO_GO, error="first run failed")
            return _crucible_outcome(VERDICT_GO)

        result = run_cycle(_issue(), config=_config(forge_fn=forge, crucible_fn=crucible))
        assert result.outcome == CycleOutcome.SUCCESS
        assert call_count["forge"] == 2  # initial + retry after NO_GO
        assert call_count["crucible"] == 2

    def test_crucible_no_go_final_failure(self) -> None:
        result = run_cycle(
            _issue(),
            config=_config(
                crucible_fn=lambda ws, n: _crucible_outcome(VERDICT_NO_GO, error="tests fail"),
            ),
        )
        assert result.outcome == CycleOutcome.NO_GO

    def test_needs_live_adds_comment_and_label(self) -> None:
        comment_calls: list[int] = []
        label_calls: list[tuple[int, str]] = []

        with patch("dark_factory.modes.auto.comment_on_issue") as mock_comment, \
             patch("dark_factory.modes.auto.add_label") as mock_add, \
             patch("dark_factory.modes.auto.remove_label"):
            mock_comment.side_effect = lambda n, body, **kw: comment_calls.append(n)
            mock_add.side_effect = lambda n, lbl, **kw: label_calls.append((n, lbl))

            result = run_cycle(
                _issue(),
                config=_config(
                    crucible_fn=lambda ws, n: _crucible_outcome(VERDICT_NEEDS_LIVE),
                ),
            )

        assert result.outcome == CycleOutcome.NEEDS_LIVE
        assert 42 in comment_calls
        assert any(lbl == LABEL_NEEDS_LIVE for _, lbl in label_calls)

    def test_deploy_failure(self) -> None:
        result = run_cycle(
            _issue(),
            config=_config(deploy_fn=lambda ws, n: False),
        )
        assert result.outcome == CycleOutcome.DEPLOY_FAILED

    def test_workspace_error(self) -> None:
        def bad_acquire(repo: str, n: int) -> Workspace:
            msg = "clone failed"
            raise RuntimeError(msg)

        result = run_cycle(
            _issue(),
            config=_config(acquire_workspace_fn=bad_acquire),
        )
        assert result.outcome == CycleOutcome.ERROR

    def test_ouroboros_called_on_success(self) -> None:
        ouroboros_calls: list[CycleOutcome] = []

        def ouroboros(issue: IssueInfo, outcome: CycleOutcome, detail: str) -> None:
            ouroboros_calls.append(outcome)

        run_cycle(_issue(), config=_config(ouroboros_fn=ouroboros))
        assert CycleOutcome.SUCCESS in ouroboros_calls

    def test_ouroboros_called_on_failure(self) -> None:
        ouroboros_calls: list[CycleOutcome] = []

        def ouroboros(issue: IssueInfo, outcome: CycleOutcome, detail: str) -> None:
            ouroboros_calls.append(outcome)

        run_cycle(
            _issue(),
            config=_config(forge_fn=lambda i, w: False, ouroboros_fn=ouroboros),
        )
        assert CycleOutcome.FORGE_FAILED in ouroboros_calls


# ── run_auto_mode ─────────────────────────────────────────────────────


class TestRunAutoMode:
    def test_processes_one_issue_and_exits(self) -> None:
        issues = [_issue(1, "First")]

        with patch("dark_factory.modes.auto.select_next_issue", side_effect=issues + [None]):
            results = run_auto_mode(config=_config(max_cycles=1))

        assert len(results) == 1
        assert results[0].outcome == CycleOutcome.SUCCESS

    def test_polls_when_no_issues(self) -> None:
        sleep_calls: list[float] = []

        def track_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        call_count = {"n": 0}

        def no_issues(**kwargs: object) -> IssueInfo | None:
            call_count["n"] += 1
            if call_count["n"] >= 3:  # noqa: PLR2004
                return _issue()
            return None

        with patch("dark_factory.modes.auto.select_next_issue", side_effect=no_issues):
            results = run_auto_mode(
                config=_config(sleep_fn=track_sleep, poll_interval=5.0, max_cycles=1),
            )

        assert len(sleep_calls) >= 2  # noqa: PLR2004
        assert all(s == 5.0 for s in sleep_calls)  # noqa: PLR2004
        assert len(results) == 1

    def test_graceful_shutdown(self) -> None:
        state = AutoModeState()
        _install_signal_handlers(state)

        # Simulate the shutdown flag being set
        state.shutdown_requested = True

        # When shutdown_requested is True before the first poll, loop exits immediately
        with patch("dark_factory.modes.auto.select_next_issue"):
            with patch("dark_factory.modes.auto._install_signal_handlers") as mock_signals:
                mock_signals.side_effect = lambda s: setattr(s, "shutdown_requested", True)
                results = run_auto_mode(config=_config(max_cycles=None))

        assert results == []

    def test_multiple_cycles(self) -> None:
        issues = [_issue(1, "First"), _issue(2, "Second"), _issue(3, "Third")]

        with patch("dark_factory.modes.auto.select_next_issue", side_effect=issues):
            results = run_auto_mode(config=_config(max_cycles=3))

        assert len(results) == 3
        assert all(r.outcome == CycleOutcome.SUCCESS for r in results)
        assert [r.issue_number for r in results] == [1, 2, 3]


# ── Signal handling ───────────────────────────────────────────────────


class TestSignalHandling:
    def test_install_sets_handlers(self) -> None:
        state = AutoModeState()
        _install_signal_handlers(state)
        assert state.shutdown_requested is False

    def test_signal_sets_shutdown_flag(self) -> None:
        import signal as sig

        state = AutoModeState()
        _install_signal_handlers(state)

        # Invoke the handler directly (simulating SIGINT)
        handler = sig.getsignal(sig.SIGINT)
        assert callable(handler)
        handler(sig.SIGINT.value, None)
        assert state.shutdown_requested is True
