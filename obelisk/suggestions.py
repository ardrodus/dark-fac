"""Post-pipeline improvement suggestion engine — ports obelisk-suggestions.sh."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.obelisk.triage import Suggestion

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.integrations.shell import CommandResult

logger = logging.getLogger(__name__)
_STATE_DIR = Path(".dark-factory")
_RATE_FILE, _SUGGEST_LOG = ".suggest-rate.json", ".obelisk-suggestions.jsonl"
_MAX_PER_DAY, _SLOW_THRESHOLD_S = 2, 1800.0
_CONTAINER_THRESHOLD, _WIREMOCK_THRESHOLD = 8, 4
_LANG_INDICATORS: dict[str, str] = {
    "Cargo.toml": "rust", "go.mod": "go", "pom.xml": "java",
    "build.gradle": "java", "Gemfile": "ruby", "requirements.txt": "python",
    "setup.py": "python", "pyproject.toml": "python", "Package.swift": "swift",
    "mix.exs": "elixir", "composer.json": "php", "pubspec.yaml": "dart",
    "package.json": "node",
}

def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())

def _get_today_count(sd: Path) -> int:
    rf = sd / _RATE_FILE
    if rf.is_file():
        try:
            return int(json.loads(rf.read_text(encoding="utf-8")).get(_today(), 0))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return 0

def _increment_rate(sd: Path) -> None:
    sd.mkdir(parents=True, exist_ok=True)
    (sd / _RATE_FILE).write_text(json.dumps(
        {_today(): _get_today_count(sd) + 1}, separators=(",", ":")), encoding="utf-8")

def _mk(detector: str, comp: str, title: str, detail: str,
        impact: str, val: float, unit: str, label: str) -> Suggestion:
    return Suggestion(detector=detector, component=comp, title=title,
                      detail=detail, impact=impact, metric_value=val,
                      metric_unit=unit, metric_label=label)

def _detect_slow_stages(metrics: Any, thr: float) -> list[Suggestion]:
    out: list[Suggestion] = []
    for name in ("workspace", "specs", "tdd", "contract", "security", "pr"):
        s = float(getattr(metrics, f"{name}_seconds", 0.0))
        if s <= thr:
            continue
        m, t = int(s / 60), int(thr / 60)
        out.append(_mk("slow_pipeline", f"factory/pipeline/{name}",
                        f"{name} stage took {m} min — consider optimization",
                        f"Stage '{name}' took {m} min (threshold: {t} min).",
                        f"Could save ~{m - t} min per run", s, "seconds", f"{name}_duration"))
    total = float(getattr(metrics, "total_seconds", 0.0))
    if total > thr and not out:
        m, t = int(total / 60), int(thr / 60)
        out.append(_mk("slow_pipeline", "factory/pipeline",
                        f"Pipeline took {m} min — consider caching/parallelism",
                        f"Total duration ({m} min) exceeded {t} min threshold.",
                        f"Could save ~{m - t} min per run", total, "seconds", "pipeline_duration"))
    return out

def _detect_container_consolidation(
    *, docker_fn: Callable[..., CommandResult] | None = None,
) -> Suggestion | None:
    from factory.integrations.shell import docker  # noqa: PLC0415
    res = (docker_fn or docker)(["ps", "--format", "{{.Names}}"], check=False)
    if res.returncode != 0:
        return None
    names = [c for c in res.stdout.strip().splitlines() if c.strip()]
    wm = [c for c in names if "wiremock" in c.lower()]
    if len(wm) >= _WIREMOCK_THRESHOLD:
        tgt = max(1, len(wm) // 3)
        return _mk("container_consolidation", "factory/twins",
                    f"{len(wm)} WireMock containers — consolidate to {tgt}",
                    f"{len(wm)} WireMock instances could share multi-service mappings.",
                    f"Reduce from {len(wm)} to ~{tgt} containers",
                    float(len(wm)), "containers", "wiremock_count")
    if len(names) >= _CONTAINER_THRESHOLD:
        return _mk("container_consolidation", "factory/twins",
                    f"{len(names)} containers — consider consolidation",
                    f"{len(names)} containers running (threshold: {_CONTAINER_THRESHOLD}).",
                    "Reduce startup time and memory usage",
                    float(len(names)), "containers", "container_count")
    return None

def _detect_missing_patterns(workspace: Path) -> Suggestion | None:
    detected = {lang for marker, lang in _LANG_INDICATORS.items()
                if (workspace / marker).exists()}
    if not detected:
        return None
    covered: set[str] = set()
    for d in (workspace / "factory" / "patterns", workspace / "factory" / "templates"):
        if d.is_dir():
            covered |= {p.stem.lower() for p in d.iterdir()}
    missing = {lang for lang in detected if not any(lang in n for n in covered)}
    if not missing:
        return None
    ns = ", ".join(sorted(missing))
    return _mk("missing_patterns", "factory/patterns",
                f"Missing factory patterns for: {ns}",
                f"Detected {ns} source files but no factory patterns/templates.",
                "Language-specific patterns improve architecture guidance",
                float(len(missing)), "languages", "missing_patterns")

def _detect_coverage_gaps(workspace: Path) -> Suggestion | None:
    gaps: list[str] = []
    if list(workspace.glob("**/*.graphql")) and not list(workspace.glob("**/test*graphql*")):
        gaps.append("GraphQL schemas without GraphQL tests")
    if list(workspace.glob("**/openapi*.yaml")) and not list(workspace.glob("**/test*contract*")):
        gaps.append("OpenAPI specs without contract tests")
    ctrls = [p for p in workspace.glob("**/*controller*")
             if "node_modules" not in str(p) and "test" not in str(p).lower()]
    if len(ctrls) > 5 and not list(workspace.glob("**/test*integration*")):
        gaps.append(f"{len(ctrls)} API controllers without integration tests")
    if not gaps:
        return None
    return _mk("test_coverage_gaps", "tests/",
                f"{len(gaps)} test coverage gap(s) detected", "; ".join(gaps),
                "Improve test coverage for API contracts",
                float(len(gaps)), "gaps", "coverage_gaps")

def _file_suggestion(sug: Suggestion, *, repo: str, dry_run: bool = False,
                     gh_fn: Callable[..., Any] | None = None) -> str:
    from factory.integrations.shell import run_command  # noqa: PLC0415
    title = f"[Obelisk Suggestion] {sug.title}"
    body = (f"## Improvement Suggestion\n\n> Auto-filed by Obelisk\n\n"
            f"**Detector:** `{sug.detector}` | **Component:** `{sug.component}`\n\n"
            f"### Description\n{sug.detail}\n\n"
            f"### Impact\n**{sug.impact}** — `{sug.metric_value} {sug.metric_unit}`\n")
    if dry_run:
        logger.info("Dry run — would file: %s", title)
        return "dry_run"
    fn = gh_fn or (lambda args, **kw: run_command(["gh", *args], **kw))
    try:
        fn(["issue", "create", "--repo", repo, "--title", title,
            "--body", body, "--label", "obelisk-suggestion,enhancement"], check=True)
        return "filed"
    except Exception:  # noqa: BLE001
        logger.warning("Failed to file suggestion issue", exc_info=True)
        return "error"

def generate_suggestions(
    metrics: Any, config: Any, *,
    workspace: Path | None = None,
    docker_fn: Callable[..., CommandResult] | None = None,
    file_issues: bool = False,
) -> list[Suggestion]:
    """Run all suggestion detectors and return findings."""
    data = getattr(config, "data", {}) if config is not None else {}
    sc = data.get("suggestions", {}) if isinstance(data, dict) else {}
    thr = float(sc.get("slow_threshold_s", _SLOW_THRESHOLD_S))
    max_day = int(sc.get("max_per_day", _MAX_PER_DAY))
    sd = Path(str(sc.get("state_dir", str(_STATE_DIR))))
    dry_run, repo = bool(sc.get("dry_run", False)), str(sc.get("repo", "pdistefano/dark-factory"))
    ws = workspace or Path.cwd()
    suggestions: list[Suggestion] = []
    for det in [lambda: _detect_slow_stages(metrics, thr),
                lambda: _detect_container_consolidation(docker_fn=docker_fn),
                lambda: _detect_missing_patterns(ws), lambda: _detect_coverage_gaps(ws)]:
        try:
            r = det()
            if isinstance(r, list):
                suggestions.extend(r)
            elif r is not None:
                suggestions.append(r)
        except Exception:  # noqa: BLE001
            logger.warning("Suggestion detector failed", exc_info=True)
    _log_suggestions(suggestions, sd)
    if file_issues and suggestions:
        filed = 0
        for s in suggestions:
            if _get_today_count(sd) >= max_day:
                logger.info("Rate limited — %d/%d filed", filed, max_day)
                break
            if _file_suggestion(s, repo=repo, dry_run=dry_run) == "filed":
                _increment_rate(sd)
                filed += 1
    return suggestions

def _log_suggestions(suggestions: list[Suggestion], sd: Path) -> None:
    if not suggestions:
        return
    sd.mkdir(parents=True, exist_ok=True)
    with (sd / _SUGGEST_LOG).open("a", encoding="utf-8") as fh:
        for s in suggestions:
            e = {"timestamp": s.timestamp, "detector": s.detector, "title": s.title}
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")
