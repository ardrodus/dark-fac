"""AnimatedSpinner widget unit tests (TS-005: UN-23 through UN-26)."""

from __future__ import annotations

import pytest

from dark_factory.ui.widgets.spinner import AnimatedSpinner, AnimatedSpinnerProtocol


class TestAnimatedSpinner:
    """UN-23 through UN-25: Frame cycling and wrap-around."""

    def test_un23_initial_frame_is_first_braille(self) -> None:
        """UN-23: Initial frame is first braille character."""
        spinner = AnimatedSpinner()
        assert spinner.current_frame == "\u280b"  # ⠋

    def test_un24_tick_advances_to_next_frame(self) -> None:
        """UN-24: tick() advances to next frame."""
        spinner = AnimatedSpinner()
        spinner.tick()
        assert spinner.current_frame == "\u2819"  # ⠙

    def test_un25_wraps_around_after_full_cycle(self) -> None:
        """UN-25: After full cycle of ticks, wraps back to first frame."""
        spinner = AnimatedSpinner()
        # Tick through all frames to wrap around
        for _ in range(10):
            spinner.tick()
        assert spinner.current_frame == "\u280b"  # ⠋ — back to start


def test_un26_conforms_to_protocol() -> None:
    """UN-26: AnimatedSpinner conforms to AnimatedSpinnerProtocol."""
    spinner = AnimatedSpinner()
    assert isinstance(spinner, AnimatedSpinnerProtocol)
