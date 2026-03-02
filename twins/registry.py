"""Twin registry and lifecycle — manifest + 3-tier cleanup (stop/clean/purge)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import builtins
    from collections.abc import Callable

    from dark_factory.integrations.shell import CommandResult

logger = logging.getLogger(__name__)
_REGISTRY_REL = Path(".dark-factory") / "twins" / "registry.json"

class TwinType(Enum):
    DB = "db"
    API = "api"
    CACHE = "cache"
    QUEUE = "queue"


class TwinStatus(Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    UNKNOWN = "unknown"

@dataclass(slots=True)
class Twin:
    """A single service twin (DB, API, cache, or queue)."""
    name: str
    type: str
    container_id: str
    compose_file: str
    status: str = TwinStatus.UNKNOWN.value
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(tz=UTC).isoformat(timespec="seconds")


def _default_docker(args: list[str]) -> CommandResult:
    from dark_factory.integrations.shell import docker  # noqa: PLC0415
    return docker(args)


def _parse_twin(name: str, raw: dict[str, Any]) -> Twin:
    return Twin(
        name=name, type=str(raw.get("type", "")),
        container_id=str(raw.get("container_id", "")),
        compose_file=str(raw.get("compose_file", "")),
        status=str(raw.get("status", TwinStatus.UNKNOWN.value)),
        created_at=str(raw.get("created_at", "")),
    )


class TwinRegistry:
    """Manages a manifest of active twins persisted to ``registry.json``."""

    def __init__(
        self, workspace: str | Path, *,
        docker_fn: Callable[..., CommandResult] | None = None,
    ) -> None:
        self._ws = Path(workspace)
        self._path = self._ws / _REGISTRY_REL
        self._dk: Callable[..., Any] = docker_fn or _default_docker
        self._twins: dict[str, Twin] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            data: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load twin registry: %s", exc)
            return
        if not isinstance(data, dict):
            return
        raw = data.get("twins", {})
        if isinstance(raw, dict):
            self._twins = {k: _parse_twin(k, v) for k, v in raw.items() if isinstance(v, dict)}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"twins": {k: asdict(v) for k, v in self._twins.items()}}
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # ── CRUD ────────────────────────────────────────────────

    def register(self, twin: Twin) -> None:
        """Add or update a twin in the registry."""
        self._twins[twin.name] = twin
        self._save()
        logger.info("Registered twin: %s (type=%s)", twin.name, twin.type)

    def unregister(self, name: str) -> bool:
        """Remove a twin by name. Returns ``True`` if it existed."""
        if name not in self._twins:
            return False
        del self._twins[name]
        self._save()
        logger.info("Unregistered twin: %s", name)
        return True

    def list(self) -> builtins.list[Twin]:
        """Return all registered twins."""
        return builtins.list(self._twins.values())

    def get(self, name: str) -> Twin | None:
        """Look up a twin by name."""
        return self._twins.get(name)

    def find_by_type(self, twin_type: str) -> builtins.list[Twin]:
        """Return twins matching *twin_type* (e.g. ``"db"``, ``"api"``)."""
        return [t for t in self._twins.values() if t.type == twin_type]

    # ── 3-tier cleanup ──────────────────────────────────────

    def stop(self, name: str | None = None) -> builtins.list[str]:
        """Tier 1 — ``docker stop``. Data preserved."""
        return self._apply(name, self._stop_one)

    def clean(self, name: str | None = None) -> builtins.list[str]:
        """Tier 2 — ``docker rm`` + volumes removed."""
        return self._apply(name, self._clean_one)

    def purge(self, name: str | None = None) -> builtins.list[str]:
        """Tier 3 — remove images + unregister."""
        names = self._apply(name, self._purge_one)
        for n in names:
            self._twins.pop(n, None)
        if names:
            self._save()
        return names

    def _apply(self, name: str | None, fn: Callable[[Twin], bool]) -> builtins.list[str]:
        targets = [self._twins[name]] if name and name in self._twins else builtins.list(self._twins.values())
        return [t.name for t in targets if fn(t)]

    def _stop_one(self, twin: Twin) -> bool:
        cid = twin.container_id
        if not cid:
            return False
        r = self._dk(["stop", cid])
        if r.returncode == 0:
            twin.status = TwinStatus.STOPPED.value
            self._save()
            logger.info("Stopped twin: %s (%s)", twin.name, cid)
            return True
        logger.warning("Failed to stop %s: %s", twin.name, r.stderr.strip())
        return False

    def _clean_one(self, twin: Twin) -> bool:
        cid = twin.container_id
        if not cid:
            return False
        self._dk(["stop", cid])
        r = self._dk(["rm", "-v", cid])
        if r.returncode == 0:
            twin.status = TwinStatus.UNKNOWN.value
            twin.container_id = ""
            self._save()
            logger.info("Cleaned twin: %s", twin.name)
            return True
        logger.warning("Failed to clean %s: %s", twin.name, r.stderr.strip())
        return False

    def _purge_one(self, twin: Twin) -> bool:
        cid = twin.container_id
        if cid:
            self._dk(["stop", cid])
            self._dk(["rm", "-v", cid])
        img = self._image_for(twin)
        if img:
            self._dk(["rmi", img])
        logger.info("Purged twin: %s", twin.name)
        return True

    @staticmethod
    def _image_for(twin: Twin) -> str:
        """Derive docker image name from the compose file."""
        if not twin.compose_file:
            return ""
        p = Path(twin.compose_file)
        if not p.is_file():
            return ""
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("image:"):
                    return line.strip().split(":", 1)[1].strip()
        except OSError:
            pass
        return ""
