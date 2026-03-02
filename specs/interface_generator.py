"""Interface definitions generator — port of generate-interfaces.sh."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from factory.setup.project_analyzer import AnalysisResult

from factory.specs.design_generator import DesignResult

logger = logging.getLogger(__name__)
_AGENT_TIMEOUT = 300
_STATE_DIR = Path(".dark-factory")


class InterfaceLang(Enum):
    TS = "ts"
    PY = "py"
    GO = "go"
    RS = "rs"
    JAVA = "java"
    JS = "js"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class InterfaceResult:
    """Structured output of interface generation."""
    language: InterfaceLang
    content: str
    validation_passed: bool
    validation_messages: tuple[str, ...] = field(default_factory=tuple)
    output_path: str = ""
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


_LANG_MAP: dict[str, InterfaceLang] = {
    "typescript": InterfaceLang.TS, "python": InterfaceLang.PY,
    "go": InterfaceLang.GO, "rust": InterfaceLang.RS,
    "java": InterfaceLang.JAVA, "kotlin": InterfaceLang.JAVA,
    "javascript": InterfaceLang.JS,
}
_META: dict[InterfaceLang, tuple[str, str]] = {
    InterfaceLang.TS: ("TypeScript Interfaces", ".ts"),
    InterfaceLang.PY: ("Python Protocol/TypedDict Specs", ".py"),
    InterfaceLang.GO: ("Go Interface Definitions", ".go"),
    InterfaceLang.RS: ("Rust Trait Definitions", ".rs"),
    InterfaceLang.JAVA: ("Java Interface Definitions", ".java"),
    InterfaceLang.JS: ("JavaScript JSDoc Specifications", ".js"),
}
_FMT: dict[InterfaceLang, str] = {
    InterfaceLang.TS: (
        "**TypeScript** (.ts): `export interface` with method sigs, "
        "`export type` for DTOs, JSDoc on all, generics, `?`/`readonly`, "
        "import statements for dependencies"),
    InterfaceLang.PY: (
        "**Python** (.py): `typing.Protocol` for interfaces, `TypedDict` for data, "
        "`from __future__ import annotations`, full type hints, "
        "docstrings with Args/Returns/Raises, Exception subclasses"),
    InterfaceLang.GO: (
        "**Go** (.go): `type X interface{}` with method sigs, "
        "`type X struct{}` with field tags for data, godoc comments, "
        "`context.Context` first param, `error` returns"),
    InterfaceLang.RS: (
        "**Rust** (.rs): `pub trait` with method sigs, "
        "`pub struct`/`pub enum` for data, `///` doc comments, "
        "`Result<T,E>` for fallible ops, `async fn` where needed"),
    InterfaceLang.JAVA: (
        "**Java** (.java): `public interface` with method sigs, "
        "record/DTO types, Javadoc on all, generics, "
        "`@Nullable` for optional, exception subclasses"),
    InterfaceLang.JS: (
        "**JavaScript** (.js): `@typedef`/`@callback` for types, "
        "`@param`/`@returns`/`@throws` on functions, "
        "`module.exports` or `export`, function stubs only"),
}
_CHECKS: dict[InterfaceLang, list[tuple[str, str]]] = {
    InterfaceLang.TS: [
        (r"(export\s+(interface|type|function)|interface\s+\w+|type\s+\w+)",
         "No TypeScript interface/type definitions")],
    InterfaceLang.PY: [
        (r"(class\s+\w+|Protocol|TypedDict|def\s+\w+)",
         "No Python Protocol/TypedDict specs")],
    InterfaceLang.GO: [
        (r"(type\s+\w+\s+interface|type\s+\w+\s+struct|func\s)",
         "No Go interface/struct definitions")],
    InterfaceLang.RS: [
        (r"(pub\s+trait\s+\w+|trait\s+\w+|pub\s+struct\s+\w+|pub\s+enum\s+\w+)",
         "No Rust trait/type definitions")],
    InterfaceLang.JAVA: [
        (r"(public\s+interface\s+\w+|interface\s+\w+|public\s+class\s+\w+)",
         "No Java interface definitions")],
    InterfaceLang.JS: [
        (r"(@typedef|@param|@returns|@callback|function\s+\w+|export\s+(function|const|class))",
         "No JavaScript JSDoc specs")],
}


def _detect_lang(analysis: object) -> InterfaceLang:
    lang = getattr(analysis, "language", "").lower()
    return _LANG_MAP.get(lang, InterfaceLang.NONE)


def _validate(content: str, lang: InterfaceLang) -> tuple[bool, list[str]]:
    if not content.strip():
        return False, ["Interface content is empty"]
    msgs = [f"FAIL: {msg}" for pat, msg in _CHECKS.get(lang, []) if not re.search(pat, content)]
    if not re.search(r"(?i)(dependency|depends|module|import|require|use |from )", content):
        msgs.append("WARN: No dependency/module relationship indicators")
    return not any(m.startswith("FAIL") for m in msgs), msgs


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _build_prompt(design: DesignResult, lang: InterfaceLang, issue: int | str) -> str:
    label = _META.get(lang, ("TypeScript Interfaces", ".ts"))[0]
    fmt = _FMT.get(lang, _FMT[InterfaceLang.TS])
    arch = "\n".join(f"- {d}" for d in design.architecture_decisions)
    comp = "\n".join(f"- {c}" for c in design.component_changes)
    api = "\n".join(f"- {c}" for c in design.api_changes)
    dm = "\n".join(f"- {c}" for c in design.data_model_changes)
    return (
        "You are an Interface Definition Generator.\n\n## Task\n\n"
        f"Produce **{label}** for issue #{issue}.\n\n"
        f"## Format\n\n{fmt}\n\n"
        "Include a MODULE DEPENDENCY MAP comment at the top.\n"
        "Do NOT include implementation — only type signatures and contracts.\n"
        "Output ONLY raw code — no markdown fences, no preamble.\n\n"
        f"## Architecture\n\n{arch}\n\n## Components\n\n{comp}\n\n"
        f"## API Changes\n\n{api}\n\n## Data Model\n\n{dm}\n\n"
        "## Rules\n\n"
        "1. Extract ALL modules/components from the design.\n"
        "2. Define complete public interface for each module.\n"
        "3. Define ALL input, output, and error types.\n"
        "4. Show dependency direction between modules.\n"
        "5. Be precise — Test Writer generates assertions from these.\n"
        "6. Do NOT invent interfaces not in the design.\n")


def _invoke_agent(prompt: str, *, invoke_fn: Callable[[str], str] | None = None) -> str:
    if invoke_fn is not None:
        return invoke_fn(prompt)
    from factory.integrations.shell import run_command  # noqa: PLC0415
    return run_command(["claude", "-p", prompt, "--output-format", "json"],
                       timeout=_AGENT_TIMEOUT, check=True).stdout


def _save(content: str, lang: InterfaceLang, num: int | str, *,
          state_dir: Path | None = None) -> Path:
    sd = (state_dir or _STATE_DIR) / "specs" / str(num) / "interfaces"
    sd.mkdir(parents=True, exist_ok=True)
    ext = _META.get(lang, ("", ".ts"))[1]
    out = sd / f"interfaces{ext}"
    out.write_text(content, encoding="utf-8")
    logger.info("Interface definitions saved to %s", out)
    return out


def _err(lang: InterfaceLang, raw: str = "", e: str = "") -> InterfaceResult:
    return InterfaceResult(language=lang, content="", validation_passed=False,
                           raw_output=raw, errors=(e,) if e else ())


def generate_interfaces(  # noqa: PLR0913
    design: DesignResult, analysis: AnalysisResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None, issue_number: int | str = 0,
) -> InterfaceResult:
    """Generate typed interface definitions from *design* and *analysis*."""
    lang = _detect_lang(analysis)
    if lang == InterfaceLang.NONE:
        lang = InterfaceLang.TS if design.component_changes else InterfaceLang.NONE
    if lang == InterfaceLang.NONE:
        return InterfaceResult(language=lang, content="", validation_passed=True,
                               validation_messages=("No language detected — skipped",))
    try:
        raw = _invoke_agent(_build_prompt(design, lang, issue_number), invoke_fn=invoke_fn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Interface agent failed: %s", exc)
        return _err(lang, e=str(exc))
    content = _strip_fences(raw)
    passed, msgs = _validate(content, lang)
    out = _save(content, lang, issue_number, state_dir=state_dir)
    return InterfaceResult(language=lang, content=content, validation_passed=passed,
                           validation_messages=tuple(msgs), output_path=str(out),
                           raw_output=raw)
