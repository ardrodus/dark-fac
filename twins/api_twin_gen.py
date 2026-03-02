"""HTTP API twin generation — WireMock stubs from API contracts (US-704).

Port of ``http-api-twin-gen.sh``.  Generates WireMock container configs with
multi-scenario stubs from OpenAPI / GraphQL contract specs.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from dark_factory.specs.api_contract_generator import ContractResult, ContractType

logger = logging.getLogger(__name__)
_WIREMOCK_IMAGE = "wiremock/wiremock:3.3.1"
_DELAY_MS = 30_000
_SCENARIOS = ("happy", "auth-failure", "rate-limit", "server-error", "timeout")
_KNOWN_APIS: tuple[tuple[str, str], ...] = (
    ("stripe", "STRIPE_API_URL"), ("twilio", "TWILIO_API_URL"),
    ("sendgrid", "SENDGRID_API_URL"), ("github", "GITHUB_API_URL"),
    ("slack", "SLACK_API_URL"), ("auth0", "AUTH0_API_URL"),
    ("okta", "OKTA_API_URL"), ("mailgun", "MAILGUN_API_URL"),
    ("plaid", "PLAID_API_URL"), ("paypal", "PAYPAL_API_URL"),
    ("shopify", "SHOPIFY_API_URL"), ("zendesk", "ZENDESK_API_URL"),
    ("braintree", "BRAINTREE_API_URL"), ("square", "SQUARE_API_URL"),
    ("intercom", "INTERCOM_API_URL"), ("datadog", "DATADOG_API_URL"),
    ("pagerduty", "PAGERDUTY_API_URL"),
)


@dataclass(frozen=True, slots=True)
class TwinConfig:
    """Result of HTTP API twin generation."""
    compose_fragment: str
    mapping_files: dict[str, str]
    scenarios: tuple[str, ...] = _SCENARIOS
    service_name: str = ""
    env_overrides: dict[str, str] = field(default_factory=dict)
    errors: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class _Endpoint:
    method: str
    path: str
    description: str = ""
    response_body: dict[str, Any] = field(default_factory=dict)
    webhook_url: str = ""
    webhook_body: dict[str, Any] = field(default_factory=dict)
    webhook_delay_ms: int = 1000


def _extract_openapi(spec: str) -> list[_Endpoint]:
    eps: list[_Endpoint] = []
    in_paths, cur = False, ""
    for line in spec.splitlines():
        s = line.rstrip()
        if re.match(r"^paths:\s*$", s):
            in_paths = True
            continue
        if in_paths and s and not s[0].isspace():
            break
        if not in_paths:
            continue
        pm = re.match(r"^  (/\S*):\s*$", s)
        if pm:
            cur = pm.group(1)
            continue
        mm = re.match(r"^    (get|post|put|patch|delete|options|head):\s*$", s)
        if mm and cur:
            eps.append(_Endpoint(method=mm.group(1).upper(), path=cur,
                                 description=f"{mm.group(1).upper()} {cur}",
                                 response_body={"status": "ok"}))
    return eps


def _extract_graphql(spec: str) -> list[_Endpoint]:
    eps: list[_Endpoint] = []
    for m in re.finditer(r"type\s+(?:Query|Mutation)\s*\{(.*?)\}", spec, re.S):
        for fm in re.finditer(r"(\w+)\s*(?:\([^)]*\))?\s*:", m.group(1)):
            name = fm.group(1)
            eps.append(_Endpoint(method="POST", path="/graphql",
                                 description=f"GraphQL: {name}",
                                 response_body={"data": {name: None}}))
    return eps


def _extract(contract: ContractResult) -> list[_Endpoint]:
    if contract.contract_type == ContractType.OPENAPI:
        return _extract_openapi(contract.spec_content)
    if contract.contract_type == ContractType.GRAPHQL:
        return _extract_graphql(contract.spec_content)
    return []


def _slug(method: str, path: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]", "-", path.lstrip("/"))
    return f"{method.lower()}-{re.sub(r'-{2,}', '-', s).strip('-') or 'root'}"


def _err_body(kind: str, msg: str, code: str) -> dict[str, Any]:
    return {"error": {"type": kind, "message": msg, "code": code}}


def _req(method: str, path: str, scenario: str, *, default: bool) -> dict[str, Any]:
    hdr: dict[str, Any] = ({"or": [{"absent": True}, {"equalTo": scenario}]}
                            if default else {"equalTo": scenario})
    return {"method": method, "urlPathPattern": path, "headers": {"X-Test-Scenario": hdr}}


def _webhook_action(ep: _Endpoint) -> list[dict[str, Any]]:
    return [{"name": "webhook", "parameters": {
        "method": "POST", "url": ep.webhook_url,
        "delay": {"type": "fixed", "milliseconds": ep.webhook_delay_ms},
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(ep.webhook_body)}}]


def _stubs(ep: _Endpoint, svc: str) -> dict[str, str]:
    """Build 5 scenario stubs (happy, auth-failure, rate-limit, server-error, timeout)."""
    p = _slug(ep.method, ep.path)
    ok: dict[str, Any] = {"status": 200, "headers": {"Content-Type": "application/json"},
                           "jsonBody": ep.response_body or {"status": "ok"}}
    happy: dict[str, Any] = {"priority": 10,
        "request": _req(ep.method, ep.path, "happy", default=True), "response": ok}
    if ep.webhook_url:
        happy["postServeActions"] = _webhook_action(ep)
    _m = json.dumps
    return {
        f"{p}-happy.json": _m(happy, indent=2),
        f"{p}-auth-failure.json": _m({"priority": 5,
            "request": _req(ep.method, ep.path, "auth-failure", default=False),
            "response": {"status": 401, "headers": {"Content-Type": "application/json"},
                "jsonBody": _err_body("authentication_error",
                    f"Invalid API key for {svc}", "api_key_invalid")}}, indent=2),
        f"{p}-rate-limit.json": _m({"priority": 5,
            "request": _req(ep.method, ep.path, "rate-limit", default=False),
            "response": {"status": 429,
                "headers": {"Content-Type": "application/json", "Retry-After": "60"},
                "jsonBody": _err_body("rate_limit_error",
                    f"Rate limit exceeded for {svc}", "rate_limit_exceeded")}}, indent=2),
        f"{p}-server-error.json": _m({"priority": 5,
            "request": _req(ep.method, ep.path, "server-error", default=False),
            "response": {"status": 500, "headers": {"Content-Type": "application/json"},
                "jsonBody": _err_body("api_error",
                    f"Internal server error from {svc}", "internal_error")}}, indent=2),
        f"{p}-timeout.json": _m({"priority": 5,
            "request": _req(ep.method, ep.path, "timeout", default=False),
            "response": {**ok, "fixedDelayMilliseconds": _DELAY_MS}}, indent=2),
    }


def _compose(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    return (
        f"  df-twin-{s}:\n    image: {_WIREMOCK_IMAGE}\n"
        f"    ports:\n      - \"8080\"\n"
        f"    volumes:\n"
        f"      - ./twin-data/{s}/mappings:/home/wiremock/mappings\n"
        f"      - ./twin-data/{s}/__files:/home/wiremock/__files\n"
        f"    healthcheck:\n"
        f"      test: [\"CMD\", \"curl\", \"-f\", \"http://localhost:8080/__admin/health\"]\n"
        f"      interval: 5s\n      timeout: 3s\n      retries: 10\n")


def _env_var(name: str) -> str:
    lo = name.lower()
    for pat, var in _KNOWN_APIS:
        if pat in lo:
            return var
    return name.upper().replace("-", "_") + "_URL"


def generate_api_twin(contract: ContractResult, *, service_name: str = "") -> TwinConfig:
    """Generate a WireMock twin config from an API contract.

    Produces WireMock mappings with 5 scenarios per endpoint (happy path, auth
    failure, rate limit, server error, timeout) plus a docker-compose fragment.
    """
    svc = service_name or "api-service"
    endpoints = _extract(contract)
    if not endpoints:
        endpoints = [_Endpoint(method="ANY", path="/.*",
                               description=f"Catch-all for {svc}",
                               response_body={"status": "ok", "service": svc,
                                               "message": "Mock response from WireMock twin"})]
        logger.info("No endpoints extracted — using catch-all stubs for '%s'", svc)
    mappings: dict[str, str] = {}
    for ep in endpoints:
        mappings.update(_stubs(ep, svc))
    ev = _env_var(svc)
    logger.info("Generated %d mappings for '%s' (%d endpoints)", len(mappings), svc, len(endpoints))
    return TwinConfig(
        compose_fragment=_compose(svc), mapping_files=mappings,
        scenarios=_SCENARIOS, service_name=svc,
        env_overrides={ev: f"http://df-twin-{svc}:8080"},
    )
