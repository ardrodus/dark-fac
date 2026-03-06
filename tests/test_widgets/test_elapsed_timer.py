"""ElapsedTimer widget unit tests (TS-006: UN-27 through UN-31)."""

from __future__ import annotations

import pytest

from dark_factory.ui.widgets.elapsed_timer import ElapsedTimer, ElapsedTimerProtocol


class TestElapsedTimerFormatting:
    """UN-27 through UN-29: Time formatting."""

    def test_un27_update_zero_displays_0s(self) -> None:
        """UN-27: update(0) displays '0.0s'."""
        timer = ElapsedTimer()
        timer.update(0)
        assert timer.display_text == "0.0s"

    def test_un28_update_1234_displays_1_2s(self) -> None:
        """UN-28: update(1234) displays '1.2s'."""
        timer = ElapsedTimer()
        timer.update(1234)
        assert timer.display_text == "1.2s"

    def test_un29_update_125000_displays_2m_5s(self) -> None:
        """UN-29: update(125000) displays '2m 5s'."""
        timer = ElapsedTimer()
        timer.update(125000)
        assert timer.display_text == "2m 5s"


class TestElapsedTimerReset:
    """UN-30: Reset behavior."""

    def test_un30_reset_returns_to_0s(self) -> None:
        """UN-30: reset() returns display to '0.0s'."""
        timer = ElapsedTimer()
        timer.update(5000)
        timer.reset()
        assert timer.display_text == "0.0s"


def test_un31_conforms_to_protocol() -> None:
    """UN-31: ElapsedTimer conforms to ElapsedTimerProtocol."""
    timer = ElapsedTimer()
    assert isinstance(timer, ElapsedTimerProtocol)
