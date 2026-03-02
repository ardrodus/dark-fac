"""Obelisk interactive diagnostic menu — ports obelisk-menu.sh."""
from __future__ import annotations

import json, logging, sys, time  # noqa: E401
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_Out = Callable[[str], object]
_In = Callable[[str], str]

logger = logging.getLogger(__name__)
_STATE_DIR = Path(".dark-factory")
_MENU_TEXT = (
    "\n  === Obelisk — System Diagnostics ===\n\n"
    "    [h]ealth       Run full diagnostic scan\n"
    "    [d]iagnose     Run Layer 1 + Layer 2 on last failure\n"
    "    [e]vents       Recent events timeline\n"
    "    [t]wins        Twin registry status\n"
    "    [l]ogs         Tail container logs\n"
    "    [r]epair       Auto-heal playbook selection\n"
    "    [s]tats        System stats and uptime\n"
    "    [q]uit         Return to main menu\n"
)


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[return-value]
    except (json.JSONDecodeError, OSError):
        return None

def _show_health(out: _Out) -> None:
    from factory.obelisk.daemon import (
        _check_containers, _check_disk, _check_rate_limit, _check_stale_workspaces,
    )
    checks = [_check_containers(), _check_disk(), _check_rate_limit(), _check_stale_workspaces()]
    out(f"\n  {'Check':<20s} {'Status':<8s} Detail\n  " + "-" * 50 + "\n")
    for c in checks:
        out(f"  {c.name:<20s} {'OK' if c.healthy else 'FAIL':<8s} {c.detail}\n")
    ok = all(c.healthy for c in checks)
    out("  " + ("All systems healthy." if ok else "Issues detected — consider [r]epair.") + "\n")

def _show_diagnose(out: _Out) -> None:
    from factory.obelisk.diagnose import obelisk_diagnose
    from factory.obelisk.triage import TriageVerdict, triage
    log_path = _STATE_DIR / ".obelisk-triage.jsonl"
    if not log_path.is_file():
        out("\n  No triage log — no failures recorded yet.\n")
        return
    try:
        last = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        entry: dict[str, object] = json.loads(last)
    except (IndexError, json.JSONDecodeError, OSError):
        out("\n  Triage log is empty or unreadable.\n")
        return
    stage = str(entry.get("stage", "unknown"))
    raw_exit = entry.get("exit_code", 1)
    exit_code = int(raw_exit) if isinstance(raw_exit, (int, float, str)) else 1
    out(f"\n  Last failure: stage={stage}  exit={exit_code}  verdict={entry.get('verdict')}\n")
    result = triage(stage, exit_code, str(entry.get("output", "")))
    out(f"  L1 verdict: {result.verdict.value}\n")
    if result.verdict == TriageVerdict.ESCALATE_HUMAN:
        out("  Layer 2 — AI diagnosis...\n")
        diag = obelisk_diagnose(str(entry.get("output", "")),
                                {"stage": stage, "exit_code": exit_code})
        out(f"  Category: {diag.category.value}  Confidence: {diag.confidence}\n")
    else:
        out("  Layer 2 not needed — Layer 1 classified the failure.\n")

def _show_events(out: _Out) -> None:
    data = _read_json(_STATE_DIR / ".obelisk-state.json")
    if data is None:
        out("\n  No obelisk state file — daemon may not have run yet.\n")
        return
    events = data.get("recent_events", [])
    if not events or not isinstance(events, list):
        out("\n  No events recorded yet.\n")
        return
    out(f"\n  Recent Events (last 20 of {len(events)}):\n")
    for ev in events[:20]:
        if isinstance(ev, dict):
            out(f"  {ev.get('ts', '?')}  [{ev.get('type', '?')}] "
                f"{ev.get('component', '?')} — {ev.get('message', '')}\n")

def _show_twins(out: _Out) -> None:
    data = _read_json(_STATE_DIR / "twins" / "registry.json")
    if data is None:
        out("\n  No twin registry found.\n")
        return
    twins = data.get("twins", {})
    if not twins or not isinstance(twins, dict):
        out("\n  Registry is empty — no twins registered.\n")
        return
    out(f"\n  Twin Registry ({len(twins)} registered):\n")
    out(f"  {'Name':<20s} {'Type':<10s} {'Status':<12s} Created\n  " + "-" * 60 + "\n")
    for name, info in twins.items():
        if not isinstance(info, dict):
            continue
        out(f"  {name:<20s} {info.get('type', '?'):<10s} "
            f"{info.get('status', '?'):<12s} {info.get('created_at', '?')}\n")

def _show_logs(out: _Out, inp: _In) -> None:
    from factory.integrations.shell import docker
    result = docker(["ps", "--format", "{{.Names}}"], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        out("\n  No containers running.\n")
        return
    names = [n.strip() for n in result.stdout.strip().splitlines() if n.strip()]
    out("\n  Running containers:\n")
    for i, name in enumerate(names, 1):
        out(f"    {i}) {name}\n")
    try:
        sel = inp("  container number (or name)> ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not sel:
        return
    target = names[int(sel) - 1] if sel.isdigit() and 1 <= int(sel) <= len(names) else sel
    logs = docker(["logs", target, "--tail", "50"], check=False)
    out(f"\n  Last 50 lines from {target}:\n")
    for line in (logs.stdout or logs.stderr or "").splitlines():
        out(f"  {line}\n")

def _show_repair(out: _Out, inp: _In) -> None:
    from factory.obelisk.auto_heal import PLAYBOOKS, run_all, run_playbook
    out("\n  Repair Playbooks:\n")
    for i, pb in enumerate(PLAYBOOKS, 1):
        out(f"    {i}) {pb.name:<30s} {pb.trigger_condition}\n")
    out("    a) Run all\n")
    try:
        sel = inp("  playbook number (or 'a')> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if sel == "a":
        for r in run_all(force=True):
            out(f"  [{'OK' if r.success else 'FAIL'}] {r.playbook_name}: {r.detail}\n")
    elif sel.isdigit() and 1 <= int(sel) <= len(PLAYBOOKS):
        res = run_playbook(PLAYBOOKS[int(sel) - 1], force=True)
        if res:
            out(f"  [{'OK' if res.success else 'FAIL'}] {res.playbook_name}: {res.detail}\n")
    elif sel:
        out(f"  Invalid selection: {sel}\n")

def _show_stats(out: _Out) -> None:
    from factory.obelisk.daemon import _read_status
    status = _read_status()
    if status is None:
        out("\n  No daemon status — daemon may not have run yet.\n")
        return
    out(f"\n  Status: {status.system_status}  Running: {status.running}\n")
    if status.started_at:
        up = time.time() - status.started_at
        out(f"  Uptime: {int(up // 3600)}h {int((up % 3600) // 60)}m\n")
    if status.last_check:
        out(f"  Last check: {int(time.time() - status.last_check)}s ago\n")
    for chk in status.checks:
        out(f"    {chk.name:<20s} {'OK' if chk.healthy else 'FAIL':<6s} {chk.detail}\n")
    for action in status.heal_actions:
        out(f"    healed: {action}\n")

def obelisk_menu(
    *, input_fn: _In | None = None, output_fn: _Out | None = None,
    max_iterations: int | None = None,
) -> None:
    """Launch the interactive Obelisk diagnostic menu."""
    _in, _out = input_fn or input, output_fn or sys.stdout.write
    _out(_MENU_TEXT)
    handlers: dict[str, Callable[[], None]] = {
        "h": lambda: _show_health(_out), "d": lambda: _show_diagnose(_out),
        "e": lambda: _show_events(_out), "t": lambda: _show_twins(_out),
        "l": lambda: _show_logs(_out, _in), "r": lambda: _show_repair(_out, _in),
        "s": lambda: _show_stats(_out),
    }
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        try:
            key = _in("\n  obelisk> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _out("\n")
            return
        if not key:
            continue
        if key in ("q", "quit", "back", "b"):
            return
        handler = handlers.get(key)
        if handler:
            try:
                handler()
            except Exception as exc:  # noqa: BLE001
                _out(f"  Error: {exc}\n")
        else:
            _out(f"  Unknown command: '{key}'. Options: h d e t l r s q\n")
