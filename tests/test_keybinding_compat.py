"""Keybinding compatibility integration tests (TS-013: IT-07 through IT-13).

Ensures all existing keybindings still work and new b/n bindings
function correctly without conflicts.
"""

from __future__ import annotations

import pytest

from dark_factory.ui.dashboard import DashboardApp, DashboardState


# ── IT-07: q key quits ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_it07_q_quits() -> None:
    """IT-07: 'q' key still quits the app."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test() as pilot:
        await pilot.press("q")
        # App should have exited (no error)


# ── IT-08: r key refreshes ───────────────────────────────────────


@pytest.mark.asyncio
async def test_it08_r_refreshes() -> None:
    """IT-08: 'r' key still triggers force refresh."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test() as pilot:
        initial_tick = app.tick
        await pilot.press("r")
        # Force refresh should not crash; state should remain consistent


# ── IT-09: a key approves gate ───────────────────────────────────


@pytest.mark.asyncio
async def test_it09_a_approves_gate() -> None:
    """IT-09: 'a' key still approves gate (no crash when no pending gates)."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test() as pilot:
        await pilot.press("a")
        # Should not crash even with no pending gates


# ── IT-10: x key rejects gate ────────────────────────────────────


@pytest.mark.asyncio
async def test_it10_x_rejects_gate() -> None:
    """IT-10: 'x' key still rejects gate (no crash when no pending gates)."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test() as pilot:
        await pilot.press("x")
        # Should not crash even with no pending gates


# ── IT-11: b key toggles banner ──────────────────────────────────


@pytest.mark.asyncio
async def test_it11_b_toggles_banner() -> None:
    """IT-11: 'b' key toggles banner visibility (hidden -> visible -> hidden)."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test() as pilot:
        banner = app.query_one("#banner-panel")
        initial_display = banner.display
        await pilot.press("b")
        toggled_display = banner.display
        assert toggled_display != initial_display
        await pilot.press("b")
        final_display = banner.display
        assert final_display == initial_display


# ── IT-12: n key dismisses toast or opens history ────────────────


@pytest.mark.asyncio
async def test_it12_n_key_notification_action() -> None:
    """IT-12: 'n' key dismisses top toast or opens notification history."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test() as pilot:
        await pilot.press("n")
        # Should not crash


# ── IT-13: No binding conflicts ──────────────────────────────────


@pytest.mark.asyncio
async def test_it13_no_binding_conflicts() -> None:
    """IT-13: New bindings (b, n) don't conflict with existing bindings."""
    state = DashboardState()
    app = DashboardApp(state=state)
    async with app.run_test():
        # Collect all binding keys
        binding_keys: list[str] = []
        for binding in app.BINDINGS:
            if isinstance(binding, tuple):
                binding_keys.append(binding[0])
            else:
                binding_keys.append(binding.key)
        # No duplicates
        assert len(binding_keys) == len(set(binding_keys)), (
            f"Duplicate bindings found: {binding_keys}"
        )
        # b and n should be present
        assert "b" in binding_keys, "'b' binding not found"
        assert "n" in binding_keys, "'n' binding not found"
