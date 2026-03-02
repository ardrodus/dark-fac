"""Common spec-generator utilities — shared prompt→invoke→parse→save pattern."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
T = TypeVar("T")
AGENT_TIMEOUT = 300
STATE_DIR = Path(".dark-factory")


def tup(raw: object) -> tuple[str, ...]:
    """Coerce list-or-scalar to tuple of strings."""
    if isinstance(raw, list):
        return tuple(str(x) for x in raw)
    return (str(raw),) if raw else ()


def strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def extract_json(raw: str) -> dict[str, object]:
    """Strip fences and extract the first JSON object."""
    text = strip_fences(raw)
    m = re.search(r"\{", text)
    if m:
        text = text[m.start():]
    return json.loads(text)


def invoke_agent(
    prompt: str, *, invoke_fn: Callable[[str], str] | None = None,
) -> str:
    """Call the Claude agent (or a provided stub for testing)."""
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from dark_factory.integrations.shell import run_command  # noqa: PLC0415
    return run_command(
        ["claude", "-p", prompt, "--output-format", "json"],
        timeout=AGENT_TIMEOUT, check=True,
    ).stdout


def save_artifact(
    content: str, filename: str, issue_number: int | str,
    *, state_dir: Path | None = None, subdir: str = "",
) -> Path:
    """Write *content* to the specs directory and return the path."""
    sd = (state_dir or STATE_DIR) / "specs" / str(issue_number)
    if subdir:
        sd = sd / subdir
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / filename
    out.write_text(content, encoding="utf-8")
    logger.info("Saved %s", out)
    return out


def format_analysis(
    analysis: object,
    attrs: tuple[tuple[str, str], ...] = (
        ("language", "Language"), ("framework", "Framework"),
        ("test_cmd", "Test cmd"), ("test_dirs", "Test dirs"),
        ("source_dirs", "Source dirs"),
    ),
) -> str:
    """Build a concise summary from an AnalysisResult."""
    parts = [f"- **{lbl}:** {val}" for attr, lbl in attrs
             if (val := getattr(analysis, attr, None))]
    return "\n".join(parts) or "No analysis available."


def validate_checks(
    content: str, checks: list[tuple[str, str]],
) -> tuple[bool, list[str]]:
    """Run regex checks; return (all_passed, failure_messages)."""
    msgs = [f"FAIL: {msg}" for pat, msg in checks
            if not re.search(pat, content)]
    return len(msgs) == 0, msgs


def run_generate(
    label: str, prompt: str,
    process_fn: Callable[[str], T],
    err_fn: Callable[[str, str], T],
    *, invoke_fn: Callable[[str], str] | None = None,
) -> T:
    """Common generate flow: invoke → process → handle errors."""
    try:
        raw = invoke_agent(prompt, invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s agent failed: %s", label, exc)
        return err_fn("", str(exc))
    try:
        return process_fn(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Failed to parse %s: %s", label, exc)
        return err_fn(raw, f"parse: {exc}")
