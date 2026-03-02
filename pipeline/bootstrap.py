"""Bootstrap mode — limited pipeline (plan/implement/test) for initial setup."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.core.config_manager import ConfigData, get_config_value, resolve_config_dir
from factory.pipeline.orchestrator import PipelineConfig, run_bootstrap_pipeline
from factory.pipeline.runner import StoryContext

logger = logging.getLogger(__name__)
_DEFAULT_MAX = 5
_PROG_FILE = "bootstrap.json"
_EMPTY: dict[str, Any] = {"status": "pending", "stories": {}, "started_at": "", "completed_at": ""}


@dataclass(frozen=True, slots=True)
class StoryOutcome:
    """Result of a single bootstrap story."""
    title: str
    passed: bool
    detail: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Outcome of a full bootstrap run."""
    success: bool
    stories_attempted: int
    stories_passed: int
    outcomes: tuple[StoryOutcome, ...]
    errors: tuple[str, ...] = ()


def _prog_path(config: ConfigData | None = None) -> Path:
    """Return path to ``bootstrap.json``."""
    if config is not None and config.config_path is not None:
        return config.config_path.parent / _PROG_FILE
    return resolve_config_dir() / _PROG_FILE


def _load_prog(path: Path) -> dict[str, Any]:
    """Load bootstrap progress; return empty state on failure."""
    if not path.is_file():
        return dict(_EMPTY)
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt bootstrap.json — starting fresh")
        return dict(_EMPTY)


def _save_prog(path: Path, prog: dict[str, Any]) -> None:
    """Persist bootstrap progress."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prog, indent=2) + "\n", encoding="utf-8")


def _build_stories(config: ConfigData, limit: int) -> list[StoryContext]:
    """Build story list from config or generate defaults."""
    raw = get_config_value(config, "bootstrap.stories")
    out: list[StoryContext] = []
    if isinstance(raw, list):
        for e in raw[:limit]:
            if not isinstance(e, dict):
                continue
            ac = e.get("acceptance_criteria", ())
            ac = tuple(str(a) for a in ac) if isinstance(ac, (list, tuple)) else (str(ac),)
            out.append(StoryContext(
                title=str(e.get("title", f"bootstrap-{len(out) + 1}")),
                description=str(e.get("description", "Bootstrap story")),
                acceptance_criteria=ac,
            ))
    if not out:
        out = [StoryContext(
            title=f"bootstrap-story-{i}", acceptance_criteria=(f"Story {i} passes.",),
            description=f"Bootstrap story {i} for initial project setup.",
        ) for i in range(1, limit + 1)]
    return out[:limit]


def run_bootstrap(config: ConfigData, max_stories: int = 0) -> BootstrapResult:
    """Run the bootstrap pipeline for up to *max_stories* stories.

    Skips arch review, security, and Crucible — only plan/implement/test.
    Default story limit comes from ``bootstrap.max_stories`` config or 5.
    """
    if max_stories <= 0:
        raw_lim = get_config_value(config, "bootstrap.max_stories")
        max_stories = int(raw_lim) if isinstance(raw_lim, (int, float)) else _DEFAULT_MAX
    pp = _prog_path(config)
    prog = _load_prog(pp)
    prog["status"] = "running"
    prog["started_at"] = prog.get("started_at") or time.strftime("%Y-%m-%dT%H:%M:%S")
    _save_prog(pp, prog)

    stories = _build_stories(config, max_stories)
    outcomes: list[StoryOutcome] = []
    errors: list[str] = []
    pcfg = PipelineConfig()

    for story in stories:
        prev = prog.get("stories", {}).get(story.title)
        if isinstance(prev, dict) and prev.get("passed"):
            logger.info("Skipping already-passed story: %s", story.title)
            outcomes.append(StoryOutcome(story.title, True, "previously passed", 0.0))
            continue
        logger.info("Bootstrap: running story %r", story.title)
        t0 = time.monotonic()
        try:
            res = run_bootstrap_pipeline(story, pcfg)
            dt = round(time.monotonic() - t0, 2)
            detail = "; ".join(
                f"{s.stage.value}:{'PASS' if s.passed else 'FAIL'}"
                for s in res.pipeline_result.stages
            )
            oc = StoryOutcome(story.title, res.passed, detail, dt)
        except Exception as exc:  # noqa: BLE001
            dt = round(time.monotonic() - t0, 2)
            oc = StoryOutcome(story.title, False, str(exc), dt)
            errors.append(f"{story.title}: {exc}")
        outcomes.append(oc)
        prog.setdefault("stories", {})[story.title] = {
            "passed": oc.passed, "detail": oc.detail, "duration": oc.duration_seconds,
        }
        _save_prog(pp, prog)

    pc = sum(1 for o in outcomes if o.passed)
    ok = pc == len(outcomes) and len(outcomes) > 0
    prog.update(status="completed" if ok else "failed",
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                stories_attempted=len(outcomes), stories_passed=pc)
    _save_prog(pp, prog)
    logger.info("Bootstrap: %d/%d stories passed", pc, len(outcomes))
    return BootstrapResult(ok, len(outcomes), pc, tuple(outcomes), tuple(errors))
