"""Status reporting and metrics display for Dark Factory.

Reads pipeline, epic, and bootstrap state from ``.dark-factory/*.json``
files and formats human-readable status output for the
``dark-factory status`` CLI command.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dark_factory.ui.dashboard import ObeliskStatus

logger = logging.getLogger(__name__)

_STATE_DIR = ".dark-factory"
_PIPELINE_FILE = "pipeline.json"
_EPICS_FILE = "epics.json"
_BOOTSTRAP_FILE = "bootstrap.json"
_DISPATCH_FILE = "dispatch.json"
_OBELISK_STATUS_FILE = "obelisk-status.json"


@dataclass(frozen=True, slots=True)
class StageMetric:
    """Metric for a single pipeline stage."""

    name: str
    state: str
    duration_ms: float = 0.0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PipelineStatus:
    """Overall pipeline run status."""

    stages: tuple[StageMetric, ...]
    passed: bool
    total_duration_ms: float
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class StoryStatus:
    """Status of a single story within an epic."""

    title: str
    state: str


@dataclass(frozen=True, slots=True)
class EpicStatus:
    """Status of an epic with its child stories."""

    title: str
    stories: tuple[StoryStatus, ...]
    completed: int = field(init=False)
    total: int = field(init=False)
    pct: float = field(init=False)

    def __post_init__(self) -> None:
        total = len(self.stories)
        completed = sum(1 for s in self.stories if s.state == "completed")
        pct = (completed / total * 100.0) if total > 0 else 0.0
        object.__setattr__(self, "total", total)
        object.__setattr__(self, "completed", completed)
        object.__setattr__(self, "pct", pct)


@dataclass(frozen=True, slots=True)
class BootstrapStatus:
    """Bootstrap pipeline status (plan/implement/test subset)."""

    stages: tuple[StageMetric, ...]
    passed: bool
    total_duration_ms: float


@dataclass(frozen=True, slots=True)
class DispatchMetrics:
    """Dispatch queue metrics."""

    queued: int = 0
    in_progress: int = 0
    completed: int = 0
    failed: int = 0
    dlq_count: int = 0


def _state_dir(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return base / _STATE_DIR


def _read_json(filename: str, cwd: Path | None = None) -> dict[str, object]:
    """Read a JSON state file, returning an empty dict if missing."""
    path = _state_dir(cwd) / filename
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Could not read state file: %s", path)
        return {}
    return data if isinstance(data, dict) else {}


def _parse_stage_metric(raw: object) -> StageMetric | None:
    if not isinstance(raw, dict):
        return None
    name, state = raw.get("name"), raw.get("state")
    if not isinstance(name, str) or not isinstance(state, str):
        return None
    dur_raw = raw.get("duration_ms", 0.0)
    duration_ms = float(dur_raw) if isinstance(dur_raw, (int, float)) else 0.0
    detail_raw = raw.get("detail", "")
    detail = str(detail_raw) if detail_raw is not None else ""
    return StageMetric(name=name, state=state, duration_ms=duration_ms, detail=detail)


def _parse_stages(raw_stages: object) -> tuple[StageMetric, ...]:
    if not isinstance(raw_stages, list):
        return ()
    metrics: list[StageMetric] = []
    for item in raw_stages:
        m = _parse_stage_metric(item)
        if m is not None:
            metrics.append(m)
    return tuple(metrics)


def _parse_story(raw: object) -> StoryStatus | None:
    if not isinstance(raw, dict):
        return None
    title, state = raw.get("title"), raw.get("state")
    if not isinstance(title, str) or not isinstance(state, str):
        return None
    return StoryStatus(title=title, state=state)


def calculate_completion(stories: Sequence[StoryStatus]) -> tuple[int, int, float]:
    """Return ``(completed, total, percentage)`` for a sequence of stories."""
    total = len(stories)
    completed = sum(1 for s in stories if s.state == "completed")
    pct = (completed / total * 100.0) if total > 0 else 0.0
    return completed, total, pct


def calculate_stage_duration(stages: Sequence[StageMetric]) -> float:
    """Sum total duration across all stages in milliseconds."""
    return sum(s.duration_ms for s in stages)


def load_pipeline_status(cwd: Path | None = None) -> PipelineStatus:
    """Load pipeline status from ``.dark-factory/pipeline.json``."""
    data = _read_json(_PIPELINE_FILE, cwd)
    stages = _parse_stages(data.get("stages"))
    passed = bool(data.get("passed", False))
    attempts_raw = data.get("attempts", 1)
    attempts = int(attempts_raw) if isinstance(attempts_raw, (int, float)) else 1
    return PipelineStatus(
        stages=stages, passed=passed,
        total_duration_ms=calculate_stage_duration(stages), attempts=attempts,
    )


def load_epic_statuses(cwd: Path | None = None) -> tuple[EpicStatus, ...]:
    """Load epic statuses from ``.dark-factory/epics.json``."""
    data = _read_json(_EPICS_FILE, cwd)
    raw_epics = data.get("epics")
    if not isinstance(raw_epics, list):
        return ()
    epics: list[EpicStatus] = []
    for raw in raw_epics:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title")
        if not isinstance(title, str):
            continue
        raw_stories = raw.get("stories", [])
        stories: list[StoryStatus] = []
        if isinstance(raw_stories, list):
            for rs in raw_stories:
                s = _parse_story(rs)
                if s is not None:
                    stories.append(s)
        epics.append(EpicStatus(title=title, stories=tuple(stories)))
    return tuple(epics)


def load_bootstrap_status(cwd: Path | None = None) -> BootstrapStatus:
    """Load bootstrap pipeline status from ``.dark-factory/bootstrap.json``."""
    data = _read_json(_BOOTSTRAP_FILE, cwd)
    stages = _parse_stages(data.get("stages"))
    passed = bool(data.get("passed", False))
    return BootstrapStatus(
        stages=stages, passed=passed, total_duration_ms=calculate_stage_duration(stages),
    )


def load_dispatch_metrics(cwd: Path | None = None) -> DispatchMetrics:
    """Load dispatch queue metrics from ``.dark-factory/dispatch.json``."""
    data = _read_json(_DISPATCH_FILE, cwd)
    if not data:
        return DispatchMetrics()

    def _int(key: str) -> int:
        raw = data.get(key, 0)
        return int(raw) if isinstance(raw, (int, float)) else 0

    return DispatchMetrics(
        queued=_int("queued"), in_progress=_int("in_progress"),
        completed=_int("completed"), failed=_int("failed"), dlq_count=_int("dlq_count"),
    )


def load_obelisk_status(cwd: Path | None = None) -> ObeliskStatus:
    """Load Obelisk supervisor status from ``.dark-factory/obelisk-status.json``.

    Returns a default ``ObeliskStatus`` when the file is missing or corrupt.
    """
    from dark_factory.ui.dashboard import ObeliskInvestigation, ObeliskStatus  # noqa: PLC0415

    data = _read_json(_OBELISK_STATUS_FILE, cwd)
    if not data:
        return ObeliskStatus()

    status = str(data.get("status", "unknown"))
    pid_raw = data.get("dark_factory_pid")
    pid = int(pid_raw) if isinstance(pid_raw, (int, float)) and pid_raw is not None else None
    uptime_raw = data.get("uptime_s", 0.0)
    uptime_s = float(uptime_raw) if isinstance(uptime_raw, (int, float)) else 0.0
    crash_raw = data.get("crash_count", 0)
    crash_count = int(crash_raw) if isinstance(crash_raw, (int, float)) else 0

    raw_invs = data.get("investigations")
    investigations: list[ObeliskInvestigation] = []
    if isinstance(raw_invs, list):
        for raw in raw_invs:
            if not isinstance(raw, dict):
                continue
            inv_id = raw.get("id")
            verdict = raw.get("verdict")
            if not isinstance(inv_id, str) or not isinstance(verdict, str):
                continue
            ts_raw = raw.get("timestamp", 0.0)
            ts = float(ts_raw) if isinstance(ts_raw, (int, float)) else 0.0
            url = str(raw.get("url", ""))
            investigations.append(ObeliskInvestigation(id=inv_id, verdict=verdict, timestamp=ts, url=url))

    return ObeliskStatus(
        status=status,
        dark_factory_pid=pid,
        uptime_s=uptime_s,
        crash_count=crash_count,
        investigations=tuple(investigations),
    )


def format_stage_table(stages: Sequence[StageMetric]) -> str:
    """Format stages as a text table with visual icons."""
    if not stages:
        return "  (no stages recorded)"
    from dark_factory.ui.theme import stage_icon  # noqa: PLC0415

    lines: list[str] = []
    for s in stages:
        icon = stage_icon(s.state)
        label = s.state.upper()
        dur = f"{s.duration_ms:.1f}ms"
        detail = f" \u2014 {s.detail}" if s.detail else ""
        lines.append(f"  {icon} {label:>7}  {s.name:<15} {dur:>10}{detail}")
    return "\n".join(lines)


def format_progress_bar(completed: int, total: int, width: int = 30) -> str:
    """Render a Unicode progress bar."""
    if total == 0:
        return "\u2595" + "\u2591" * width + "\u258f 0/0 (0.0%)"
    pct = completed / total
    filled = int(pct * width)
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    return f"\u2595{bar}\u258f {completed}/{total} ({pct * 100:.1f}%)"


def show_status(cwd: Path | None = None) -> str:
    """Format overall status: pipeline + dispatch metrics."""
    ps = load_pipeline_status(cwd)
    dm = load_dispatch_metrics(cwd)
    verdict = "PASS" if ps.passed else ("FAIL" if ps.stages else "NO DATA")
    lines: list[str] = [
        "Dark Factory — Status", "=" * 40, "",
        f"Pipeline: {verdict}  (attempts: {ps.attempts})",
        f"Total duration: {ps.total_duration_ms:.1f}ms",
        "", "Stages:", format_stage_table(ps.stages),
        "", "Dispatch Queue:",
        f"  Queued:       {dm.queued}",
        f"  In progress:  {dm.in_progress}",
        f"  Completed:    {dm.completed}",
        f"  Failed:       {dm.failed}",
        f"  DLQ entries:  {dm.dlq_count}",
    ]
    return "\n".join(lines)


def show_epic_status(cwd: Path | None = None) -> str:
    """Format epic-level progress."""
    epics = load_epic_statuses(cwd)
    if not epics:
        return "No epics found in .dark-factory/epics.json"
    lines: list[str] = ["Dark Factory — Epic Status", "=" * 40]
    grand_completed, grand_total = 0, 0
    for epic in epics:
        lines.append("")
        lines.append(f"Epic: {epic.title}")
        lines.append(f"  {format_progress_bar(epic.completed, epic.total)}")
        for story in epic.stories:
            icon = "\u2714" if story.state == "completed" else "\u2504"
            lines.append(f"  {icon} {story.title} ({story.state})")
        grand_completed += epic.completed
        grand_total += epic.total
    lines.append("")
    lines.append("Overall:")
    lines.append(f"  {format_progress_bar(grand_completed, grand_total)}")
    return "\n".join(lines)


def show_bootstrap_status(cwd: Path | None = None) -> str:
    """Format bootstrap pipeline status."""
    bs = load_bootstrap_status(cwd)
    verdict = "PASS" if bs.passed else ("FAIL" if bs.stages else "NO DATA")
    lines: list[str] = [
        "Dark Factory — Bootstrap Status", "=" * 40, "",
        f"Bootstrap pipeline: {verdict}",
        f"Total duration: {bs.total_duration_ms:.1f}ms",
        "", "Stages:", format_stage_table(bs.stages),
    ]
    return "\n".join(lines)
