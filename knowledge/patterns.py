"""Pattern knowledge system — confidence, staleness, tags, conflict resolution, sharing, discovery."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)
_PATTERNS_REL = Path(".dark-factory") / "patterns"
_STALE_DAYS = 90
_DECAY = 0.95  # per-30-day confidence decay multiplier


class PatternType(Enum):
    AUTH = "auth"
    ERROR_HANDLING = "error-handling"
    API_DESIGN = "api-design"
    STATE_MANAGEMENT = "state-management"
    TESTING = "testing"
    LOGGING = "logging"
    CACHING = "caching"
    CONCURRENCY = "concurrency"
    DATA_VALIDATION = "data-validation"
    RETRY_STRATEGY = "retry-strategy"
    SECURITY = "security"
    CONFIGURATION = "configuration"
    DEPLOYMENT = "deployment"
    MESSAGING = "messaging"
    OBSERVABILITY = "observability"


@dataclass(slots=True)
class Pattern:
    """A single reusable pattern with confidence scoring and metadata."""

    name: str
    type: str
    content: str
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    last_used_at: str = ""
    source_repo: str = ""
    usage_count: int = 0

    def __post_init__(self) -> None:
        now = datetime.now(tz=UTC).isoformat(timespec="seconds")
        if not self.created_at:
            self.created_at = now
        if not self.last_used_at:
            self.last_used_at = now
        self.confidence = max(0.0, min(1.0, self.confidence))


@dataclass(slots=True)
class SharingConfig:
    """Per-repo opt-in/opt-out for cross-project pattern sharing."""

    share_patterns: bool = True
    accept_from: list[str] = field(default_factory=list)
    block_from: list[str] = field(default_factory=list)

    def is_repo_accepted(self, repo: str) -> bool:
        if repo in self.block_from:
            return False
        if self.accept_from and repo not in self.accept_from:
            return False
        return True


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def _age_days(iso_ts: str) -> int:
    """Return days since *iso_ts*, or -1 on parse failure."""
    try:
        last = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return -1
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (datetime.now(tz=UTC) - last).days


class PatternStore:
    """Manages patterns persisted to ``.dark-factory/patterns/``."""

    def __init__(self, workspace: str | Path) -> None:
        self._ws = Path(workspace)
        self._dir = self._ws / _PATTERNS_REL
        self._patterns: dict[str, Pattern] = {}
        self._sharing = SharingConfig()
        self._load()

    # ── persistence ──────────────────────────────────────────────

    def _load(self) -> None:
        pf = self._dir / "patterns.json"
        if pf.is_file():
            try:
                data = json.loads(pf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load patterns: %s", exc)
                return
            for name, raw in (data.get("patterns") or {}).items():
                if isinstance(raw, dict):
                    self._patterns[name] = Pattern(
                        name=name, type=str(raw.get("type", "")),
                        content=str(raw.get("content", "")),
                        confidence=float(raw.get("confidence", 0.5)),
                        tags=list(raw.get("tags") or []),
                        created_at=str(raw.get("created_at", "")),
                        last_used_at=str(raw.get("last_used_at", "")),
                        source_repo=str(raw.get("source_repo", "")),
                        usage_count=int(raw.get("usage_count", 0)),
                    )
        sf = self._dir / "sharing.json"
        if sf.is_file():
            try:
                sd = json.loads(sf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            self._sharing = SharingConfig(
                share_patterns=bool(sd.get("share_patterns", True)),
                accept_from=list(sd.get("accept_from") or []),
                block_from=list(sd.get("block_from") or []),
            )

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "patterns": {n: asdict(p) for n, p in self._patterns.items()}}
        (self._dir / "patterns.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _save_sharing(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "sharing.json").write_text(
            json.dumps(asdict(self._sharing), indent=2) + "\n", encoding="utf-8")

    # ── CRUD ─────────────────────────────────────────────────────

    def add(self, pattern: Pattern) -> None:
        """Add or replace a pattern."""
        self._patterns[pattern.name] = pattern
        self._save()
        logger.info("Added pattern: %s (type=%s)", pattern.name, pattern.type)

    def get(self, name: str) -> Pattern | None:
        return self._patterns.get(name)

    def list_all(self) -> list[Pattern]:
        return list(self._patterns.values())

    def remove(self, name: str) -> bool:
        if name not in self._patterns:
            return False
        del self._patterns[name]
        self._save()
        return True

    # ── search ───────────────────────────────────────────────────

    def search(
        self, *, query: str = "", tags: list[str] | None = None,
        pattern_type: str = "", min_confidence: float = 0.0,
    ) -> list[Pattern]:
        """Search patterns by query, tags, type, and/or minimum confidence."""
        results: list[Pattern] = []
        q = query.lower()
        for p in self._patterns.values():
            if p.confidence < min_confidence:
                continue
            if pattern_type and p.type != pattern_type:
                continue
            if tags and not set(tags).issubset(set(p.tags)):
                continue
            if q and q not in p.name.lower() and q not in p.content.lower():
                continue
            results.append(p)
        return sorted(results, key=lambda x: x.confidence, reverse=True)

    # ── confidence ───────────────────────────────────────────────

    def update_confidence(self, name: str, *, success: bool) -> float:
        """Adjust confidence after use. Returns new value."""
        p = self._patterns.get(name)
        if p is None:
            return 0.0
        p.usage_count += 1
        p.last_used_at = _now_iso()
        if success:
            p.confidence = min(1.0, p.confidence + 0.1 * (1.0 - p.confidence))
        else:
            p.confidence = max(0.0, p.confidence - 0.15)
        self._save()
        logger.info("Confidence %s: %.2f (success=%s)", name, p.confidence, success)
        return p.confidence

    # ── staleness ────────────────────────────────────────────────

    def prune_stale(self, *, stale_days: int = _STALE_DAYS) -> list[str]:
        """Decay confidence for patterns unused in *stale_days*. Returns affected names."""
        pruned: list[str] = []
        for p in self._patterns.values():
            age = _age_days(p.last_used_at)
            if age < stale_days:
                continue
            old = p.confidence
            p.confidence = max(0.0, p.confidence * (_DECAY ** (age // 30)))
            if p.confidence != old:
                pruned.append(p.name)
                logger.info("Stale %s: %.2f -> %.2f (%dd)", p.name, old, p.confidence, age)
        if pruned:
            self._save()
        return pruned

    # ── conflict resolution ──────────────────────────────────────

    def resolve_conflicts(self, names: list[str]) -> str:
        """Return the name with highest confidence among *names*."""
        best: Pattern | None = None
        for n in names:
            p = self._patterns.get(n)
            if p is not None and (best is None or p.confidence > best.confidence):
                best = p
        return best.name if best else ""

    # ── sharing config ───────────────────────────────────────────

    @property
    def sharing(self) -> SharingConfig:
        return self._sharing

    def set_sharing(self, config: SharingConfig) -> None:
        self._sharing = config
        self._save_sharing()

    # ── discovery report ─────────────────────────────────────────

    def export_report(self) -> str:
        """Markdown summary of all patterns with confidence and usage stats."""
        lines = [
            "# Pattern Discovery Report", "",
            f"Generated: {_now_iso()}",
            f"Total patterns: {len(self._patterns)}", "",
        ]
        if not self._patterns:
            lines.append("_No patterns recorded._")
            return "\n".join(lines)
        lines.append("| Pattern | Type | Confidence | Uses | Tags | Source | Last Used |")
        lines.append("|---------|------|------------|------|------|--------|-----------|")
        for p in sorted(self._patterns.values(), key=lambda x: x.confidence, reverse=True):
            t = ", ".join(p.tags) or "-"
            lu = p.last_used_at[:10] if p.last_used_at else "-"
            lines.append(f"| {p.name} | {p.type} | {p.confidence:.2f} | {p.usage_count} "
                         f"| {t} | {p.source_repo or '-'} | {lu} |")
        lines.append("")
        stale = [p.name for p in self._patterns.values() if _age_days(p.last_used_at) >= _STALE_DAYS]
        if stale:
            lines += [f"## Stale Patterns ({len(stale)})", ""]
            lines += [f"- {s}" for s in stale]
            lines.append("")
        hi = sum(1 for p in self._patterns.values() if p.confidence >= 0.7)
        md = sum(1 for p in self._patterns.values() if 0.3 <= p.confidence < 0.7)
        lo = sum(1 for p in self._patterns.values() if p.confidence < 0.3)
        lines += ["## Confidence Distribution", "",
                   f"- High (>= 0.7): {hi}", f"- Medium (0.3 - 0.7): {md}",
                   f"- Low (< 0.3): {lo}", ""]
        return "\n".join(lines)
