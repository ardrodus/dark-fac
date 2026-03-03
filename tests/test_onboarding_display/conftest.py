"""Shared test infrastructure for onboarding display tests.

Provides MockPresenter, OnboardingDisplayConfig, and shared fixtures
for DI-based testing of styled onboarding output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest


class MockPresenter:
    """Mock implementation of OnboardingPresenter capturing all 9 method calls.

    Each call is recorded as a (method_name, args, kwargs) tuple in the
    ``calls`` list for post-hoc assertion.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls[len(self.calls):] = [(method, args, kwargs)]

    def show_banner(self) -> None:
        self._record("show_banner")

    def phase_header(self, step: int, total: int, title: str, *, pillar: str = "") -> None:
        self._record("phase_header", step, total, title, pillar=pillar)

    def status_line(self, text: str, level: str = "info") -> None:
        self._record("status_line", text, level=level)

    def dep_status(self, name: str, found: bool) -> None:
        self._record("dep_status", name, found)

    def completion_panel(self, repo: str, strategy: str, label_count: int) -> None:
        self._record("completion_panel", repo, strategy, label_count)

    def error(self, message: str, *, hint: str = "") -> None:
        self._record("error", message, hint=hint)

    def menu(self, options: list[tuple[str, str]], *, recommended: str = "") -> None:
        self._record("menu", options, recommended=recommended)

    def stage_result(self, name: str, state: str, detail: str = "") -> None:
        self._record("stage_result", name, state, detail)

    def verdict_tag(self, state: str) -> str:
        self._record("verdict_tag", state)
        return f"[{state.upper()}]"

    def get_calls(self, method: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        """Return (args, kwargs) for all calls to *method*."""
        return [(args, kwargs) for m, args, kwargs in self.calls if m == method]


@dataclass
class OnboardingDisplayConfig:
    """DI configuration wiring writer and presenter for onboarding output.

    Parameters
    ----------
    writer:
        Callable accepting a single string argument for raw text output.
    presenter:
        MockPresenter (or real OnboardingPresenter) for styled display calls.
    use_rich:
        Whether to use Rich markup in output.
    """

    writer: Any = None
    presenter: MockPresenter | None = None
    use_rich: bool = True


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def captured_output() -> list[str]:
    """Provide a list[str] for writer injection -- captures all raw output."""
    return []


@pytest.fixture()
def mock_presenter() -> MockPresenter:
    """Provide a fresh MockPresenter instance capturing all 9 method calls."""
    return MockPresenter()


@pytest.fixture()
def display_config(captured_output: list[str], mock_presenter: MockPresenter) -> OnboardingDisplayConfig:
    """Create OnboardingDisplayConfig with writer=captured.append and mock presenter."""
    return OnboardingDisplayConfig(
        writer=captured_output.append,
        presenter=mock_presenter,
    )


@pytest.fixture()
def mock_gh_cmd():
    """Patch gh_cmd() to return success without network calls."""

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch("dark_factory.integrations.shell.gh", return_value=_FakeResult()) as m:
        yield m


@pytest.fixture()
def mock_input():
    """Patch input() with predetermined responses.

    Returns a list that tests can populate with responses before triggering
    code that calls input().  Responses are consumed in FIFO order.
    """
    responses: list[str] = []

    def _fake_input(prompt: str = "") -> str:
        if responses:
            return responses.pop(0)
        return ""

    with patch("builtins.input", side_effect=_fake_input):
        yield responses
