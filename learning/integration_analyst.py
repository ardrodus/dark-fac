"""Integration Analyst agent — discovers external services, API clients, webhooks, queues, caches."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.learning.api_explorer import APIExplorerResult
    from factory.workspace.manager import Workspace

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT, _STATE_DIR = 300, Path(".dark-factory")
_SRC_EXTS = frozenset((".py", ".js", ".ts", ".go", ".rs", ".java", ".cs", ".kt", ".rb", ".php"))
_EXCLUDE = frozenset(
    "node_modules .git vendor dist __pycache__ .tox .venv .mypy_cache coverage build "
    "target .next .nuxt venv env .dark-factory".split())
_HTTP_KW = (
    "axios", "fetch", "node-fetch", "got", "superagent", "undici",
    "requests", "httpx", "aiohttp", "urllib3",
    "HttpClient", "RestTemplate", "WebClient", "OkHttp", "Retrofit",
    "reqwest", "hyper", "Faraday", "RestSharp", "Flurl",
)
_SDK_KW = (
    "aws-sdk", "boto3", "@azure", "google-cloud", "stripe", "twilio",
    "sendgrid", "auth0", "okta", "sentry", "datadog",
)
_QUEUE_KW = (
    "amqplib", "pika", "bunny", "kafkajs", "confluent-kafka", "bullmq",
    "celery", "resque", "sidekiq", "rabbitmq", "kafka",
)
_CACHE_KW = ("redis", "ioredis", "memcached", "elasticache", "cache")
_WEBHOOK_RE = re.compile(r"webhook|signature.verif|hmac.verify|event.hook", re.IGNORECASE)
_INTEGRATION_RE = re.compile(
    "|".join(re.escape(k) for k in (*_HTTP_KW[:10], *_SDK_KW[:6], *_QUEUE_KW[:5], *_CACHE_KW[:3])),
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    """Structured output of the Integration Analyst agent."""
    external_services: tuple[str, ...] = ()
    api_clients: tuple[str, ...] = ()
    webhooks: tuple[str, ...] = ()
    message_queues: tuple[str, ...] = ()
    cache_layers: tuple[str, ...] = ()
    raw_output: str = ""
    errors: tuple[str, ...] = ()


def _rd(p: Path, limit: int = 6000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""

def _strs(data: dict[str, object], key: str) -> tuple[str, ...]:
    raw = data.get(key)
    return tuple(str(e) for e in raw) if isinstance(raw, list) else ()

def _collect_context(ws: Path, api: APIExplorerResult) -> str:
    parts: list[str] = []
    if api.endpoints:
        parts.append(f"## Known API endpoints ({len(api.endpoints)} total)")
        for ep in api.endpoints[:10]:
            parts.append(f"  {ep.method} {ep.path} -> {ep.handler} ({ep.file})")
        parts.append("")
    if api.auth_mechanisms:
        parts.append(f"Auth mechanisms: {', '.join(api.auth_mechanisms)}\n")
    if api.middleware:
        parts.append(f"Middleware: {', '.join(api.middleware)}\n")
    # Find files with integration keywords
    seen: set[Path] = set()
    for f in ws.rglob("*"):
        if not (f.is_file() and f.suffix.lower() in _SRC_EXTS):
            continue
        if any(p in _EXCLUDE for p in f.parts):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")[:6000]
        except OSError:
            continue
        if _INTEGRATION_RE.search(content) or _WEBHOOK_RE.search(content):
            if len(seen) < 25:  # noqa: PLR2004
                seen.add(f)
                parts.append(f"## {f.relative_to(ws)}\n{content}\n")
    # Check for env templates
    for name in (".env.example", ".env.template", ".env.sample", "docker-compose.yml"):
        content = _rd(ws / name)
        if content:
            parts.append(f"## {name}\n{content}\n")
    # Check manifests for dependency hints
    for name in ("package.json", "requirements.txt", "pyproject.toml", "Cargo.toml", "go.mod"):
        content = _rd(ws / name)
        if content:
            parts.append(f"## {name}\n{content}\n")
    return "\n".join(parts)

def _build_prompt(context: str) -> str:
    return f"""\
You are the Integration Analyst Agent. Analyze the workspace data below and produce a JSON object \
documenting all external integrations found.

{context}

## Output Format (strict JSON)
{{"external_services": ["AWS S3 — file storage via boto3", ...],\
 "api_clients": ["Stripe API — stripe-python SDK v5.x", ...],\
 "webhooks": ["POST /webhooks/stripe — payment event handler", ...],\
 "message_queues": ["RabbitMQ via pika — order processing", ...],\
 "cache_layers": ["Redis via ioredis — session cache", ...]}}

Guidelines:
- external_services: every third-party service dependency with purpose
- api_clients: HTTP/SDK clients with library and version if detectable
- webhooks: inbound and outbound webhook endpoints
- message_queues: message brokers, job queues, event buses
- cache_layers: caching systems and what they cache
- Be EXHAUSTIVE — list ALL integrations found. Empty array if none.
Output ONLY the JSON object, no markdown fences."""

def _invoke_agent(
    prompt: str, workspace_path: str, *,
    invoke_fn: Callable[[str], str] | None = None,
) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415
    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=_AGENT_TIMEOUT, check=True, cwd=workspace_path,
    ).stdout

def _parse_result(raw: str) -> IntegrationResult:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\{.*\"external_services\".*\}", text, re.DOTALL)
    if not match:
        match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return IntegrationResult(raw_output=raw, errors=("no JSON found in output",))
    data: dict[str, object] = json.loads(match.group(0))
    return IntegrationResult(
        external_services=_strs(data, "external_services"),
        api_clients=_strs(data, "api_clients"),
        webhooks=_strs(data, "webhooks"),
        message_queues=_strs(data, "message_queues"),
        cache_layers=_strs(data, "cache_layers"),
        raw_output=raw,
    )

def _save(result: IntegrationResult, repo_name: str, *, state_dir: Path | None = None) -> Path:
    sd = (state_dir or _STATE_DIR) / "learning" / repo_name
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / "integration_analyst.json"
    payload = {k: v for k, v in asdict(result).items() if k != "raw_output"}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Integration Analyst results saved to %s", out)
    return out

def run_integration_analyst(
    workspace: Workspace, api: APIExplorerResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None,
) -> IntegrationResult:
    """Discover external services, API clients, webhooks, message queues, and cache layers."""
    ws_path = Path(workspace.path)
    repo_name = workspace.name or ws_path.name
    context = _collect_context(ws_path, api)
    prompt = _build_prompt(context)
    try:
        raw = _invoke_agent(prompt, workspace.path, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Integration Analyst agent failed: %s", exc)
        return IntegrationResult(raw_output="", errors=(str(exc),))
    try:
        result = _parse_result(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Integration Analyst parse error: %s", exc)
        return IntegrationResult(raw_output=raw, errors=(f"parse error: {exc}",))
    _save(result, repo_name, state_dir=state_dir)
    return result
