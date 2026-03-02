"""LocalStack dev-mode startup and health check.

Starts LocalStack via ``docker compose``, waits for health, and sets
AWS test credentials in the environment.  Logs a warning and returns
``False`` when LocalStack is unavailable.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

_HEALTH_TIMEOUT = 30.0
_HEALTH_POLL = 2.0
_LOCALSTACK_ENDPOINT_DEFAULT = "http://localhost:4566"


def _is_localstack_healthy(endpoint: str) -> bool:
    """Return ``True`` if LocalStack responds to a health probe."""
    try:
        import urllib.request

        url = f"{endpoint}/_localstack/health"
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            return resp.status == 200  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return False


def _set_dev_env(endpoint: str) -> None:
    """Inject LocalStack test credentials into the process environment."""
    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["AWS_ACCOUNT_ID"] = "000000000000"
    os.environ["LOCALSTACK_ENDPOINT"] = endpoint
    os.environ["DEV_MODE"] = "true"


def dev_mode_startup() -> bool:
    """Start LocalStack and configure dev-mode environment.

    Returns ``True`` when LocalStack is healthy and credentials are set,
    ``False`` otherwise (with a warning logged).
    """
    endpoint = os.environ.get("LOCALSTACK_ENDPOINT", _LOCALSTACK_ENDPOINT_DEFAULT)

    # Fast path: already running
    if _is_localstack_healthy(endpoint):
        logger.info("LocalStack already healthy at %s", endpoint)
        _set_dev_env(endpoint)
        return True

    # Try to start via docker compose
    docker = shutil.which("docker")
    if docker is None:
        logger.warning("docker not found — cannot start LocalStack")
        return False

    logger.info("Starting LocalStack via docker compose...")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "localstack"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to start LocalStack via docker compose")
        return False

    # Wait for health
    deadline = time.monotonic() + _HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        if _is_localstack_healthy(endpoint):
            logger.info("LocalStack healthy at %s", endpoint)
            _set_dev_env(endpoint)
            return True
        time.sleep(_HEALTH_POLL)

    logger.warning("LocalStack did not become healthy within %.0fs", _HEALTH_TIMEOUT)
    return False
