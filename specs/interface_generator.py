"""Interface definitions generator — port of generate-interfaces.sh."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from dark_factory.setup.project_analyzer import AnalysisResult

from dark_factory.specs.base import run_generate, save_artifact, strip_fences
from dark_factory.specs.design_generator import DesignResult


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

# (label, extension, format_instructions, validation_regex)
_SPEC: dict[InterfaceLang, tuple[str, str, str, str]] = {
    InterfaceLang.TS: (
        "TypeScript Interfaces", ".ts",
        "**TypeScript** (.ts): `export interface` with method sigs, `export type` for DTOs,"
        " JSDoc on all, generics, `?`/`readonly`, import statements for dependencies",
        r"(export\s+(interface|type|function)|interface\s+\w+|type\s+\w+)"),
    InterfaceLang.PY: (
        "Python Protocol/TypedDict Specs", ".py",
        "**Python** (.py): `typing.Protocol` for interfaces, `TypedDict` for data,"
        " `from __future__ import annotations`, full type hints, docstrings with Args/Returns/Raises,"
        " Exception subclasses",
        r"(class\s+\w+|Protocol|TypedDict|def\s+\w+)"),
    InterfaceLang.GO: (
        "Go Interface Definitions", ".go",
        "**Go** (.go): `type X interface{}` with method sigs, `type X struct{}` with field tags"
        " for data, godoc comments, `context.Context` first param, `error` returns",
        r"(type\s+\w+\s+interface|type\s+\w+\s+struct|func\s)"),
    InterfaceLang.RS: (
        "Rust Trait Definitions", ".rs",
        "**Rust** (.rs): `pub trait` with method sigs, `pub struct`/`pub enum` for data,"
        " `///` doc comments, `Result<T,E>` for fallible ops, `async fn` where needed",
        r"(pub\s+trait\s+\w+|trait\s+\w+|pub\s+struct\s+\w+|pub\s+enum\s+\w+)"),
    InterfaceLang.JAVA: (
        "Java Interface Definitions", ".java",
        "**Java** (.java): `public interface` with method sigs, record/DTO types,"
        " Javadoc on all, generics, `@Nullable` for optional, exception subclasses",
        r"(public\s+interface\s+\w+|interface\s+\w+|public\s+class\s+\w+)"),
    InterfaceLang.JS: (
        "JavaScript JSDoc Specifications", ".js",
        "**JavaScript** (.js): `@typedef`/`@callback` for types, `@param`/`@returns`/`@throws`"
        " on functions, `module.exports` or `export`, function stubs only",
        r"(@typedef|@param|@returns|@callback|function\s+\w+|export\s+(function|const|class))"),
}


def _validate(content: str, lang: InterfaceLang) -> tuple[bool, list[str]]:
    if not content.strip():
        return False, ["Interface content is empty"]
    spec = _SPEC.get(lang)
    msgs: list[str] = []
    if spec and not re.search(spec[3], content):
        msgs.append(f"FAIL: No {spec[0].split()[0]} definitions found")
    if not re.search(r"(?i)(dependency|depends|module|import|require|use |from )", content):
        msgs.append("WARN: No dependency/module relationship indicators")
    return not any(m.startswith("FAIL") for m in msgs), msgs


def _build_prompt(design: DesignResult, lang: InterfaceLang, issue: int | str) -> str:
    spec = _SPEC.get(lang, _SPEC[InterfaceLang.TS])
    arch = "\n".join(f"- {d}" for d in design.architecture_decisions)
    comp = "\n".join(f"- {c}" for c in design.component_changes)
    api = "\n".join(f"- {c}" for c in design.api_changes)
    dm = "\n".join(f"- {c}" for c in design.data_model_changes)
    return (
        "You are an Interface Definition Generator.\n\n## Task\n\n"
        f"Produce **{spec[0]}** for issue #{issue}.\n\n"
        f"## Format\n\n{spec[2]}\n\n"
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
        "6. Do NOT invent interfaces not in the design.\n"
        "7. If the feature does not involve new or changed interfaces, respond with"
        " NO_OPINION_NEEDED. Do not manufacture interface definitions when the"
        " feature is entirely outside your domain.\n")


def _process(raw: str, lang: InterfaceLang, issue_number: int | str,
             state_dir: Path | None) -> InterfaceResult:
    content = strip_fences(raw)
    passed, msgs = _validate(content, lang)
    spec = _SPEC.get(lang, _SPEC[InterfaceLang.TS])
    out = save_artifact(content, f"interfaces{spec[1]}", issue_number,
                        state_dir=state_dir, subdir="interfaces")
    return InterfaceResult(language=lang, content=content, validation_passed=passed,
                           validation_messages=tuple(msgs), output_path=str(out),
                           raw_output=raw)


def _err(lang: InterfaceLang, raw: str = "", e: str = "") -> InterfaceResult:
    return InterfaceResult(language=lang, content="", validation_passed=False,
                           raw_output=raw, errors=(e,) if e else ())


def generate_interfaces(  # noqa: PLR0913
    design: DesignResult, analysis: AnalysisResult, *,
    invoke_fn: Callable[[str], str] | None = None,
    state_dir: Path | None = None, issue_number: int | str = 0,
) -> InterfaceResult:
    """Generate typed interface definitions from *design* and *analysis*."""
    lang = _LANG_MAP.get(getattr(analysis, "language", "").lower(), InterfaceLang.NONE)
    if lang == InterfaceLang.NONE:
        lang = InterfaceLang.TS if design.component_changes else InterfaceLang.NONE
    if lang == InterfaceLang.NONE:
        return InterfaceResult(language=lang, content="", validation_passed=True,
                               validation_messages=("No language detected — skipped",))
    return run_generate(
        "Interface", _build_prompt(design, lang, issue_number),
        lambda raw: _process(raw, lang, issue_number, state_dir),
        lambda raw, e: _err(lang, raw, e),
        invoke_fn=invoke_fn,
    )
