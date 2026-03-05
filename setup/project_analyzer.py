"""Project analysis result type and display helpers.

Architecture
~~~~~~~~~~~~
The actual analysis is performed by the ``project_analysis`` DOT pipeline
(``pipelines/project_analysis.dot``), NOT by Python heuristics.  The pipeline
uses an LLM agent to read the repo and output JSON matching :class:`AnalysisResult`.

This module is a **data contract + display layer**.  It provides:

- :class:`AnalysisResult` — frozen dataclass, the output contract between the
  pipeline and the orchestrator.  Fields are parsed from pipeline JSON output
  in ``orchestrator._run_project_analysis()``.
- :func:`display_analysis_results` — prints analysis to terminal.
- :func:`confirm_or_override_analysis` — interactive override prompt (non-auto mode).

**Do NOT add detection heuristics here.**  If analysis logic needs to change,
modify ``pipelines/project_analysis.dot`` instead.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Structured result of project analysis."""

    language: str = ""
    framework: str = ""
    detected_app_type: str = "console"
    confidence: str = "low"
    description: str = ""
    build_cmd: str = ""
    test_cmd: str = ""
    run_cmd: str = ""
    base_image: str = "debian:bookworm-slim"
    required_tools: tuple[str, ...] = ()
    source_dirs: tuple[str, ...] = ("src/",)
    test_dirs: tuple[str, ...] = ("tests/",)
    has_web_server: bool = False
    has_database: bool = False
    has_iac: bool = False
    aws_services: tuple[str, ...] = ()


def display_analysis_results(result: AnalysisResult) -> None:
    """Format and display analysis results to the terminal."""
    from dark_factory.ui.cli_colors import cprint, styled  # noqa: PLC0415

    cprint("")
    cprint(styled("  Project Analysis Results", "info"))
    cprint(f"  [dim]{'─' * 50}[/]")

    if result.description:
        cprint(f"  {result.description}", "muted")
        cprint("")

    lang = (f"{result.language} / {result.framework}" if result.framework
            else (result.language or "unknown"))
    cprint(f"  Language:     {styled(lang, 'success')}")
    if result.base_image:
        cprint(f"  Base image:   {result.base_image}", "muted")

    conf_level = "success" if result.confidence == "high" else "warning" if result.confidence == "medium" else "error"
    cprint(f"  App type:     {styled(result.detected_app_type, conf_level)} (confidence: {result.confidence})")

    chars = [c for c, v in (("web-server", result.has_web_server),
             ("database", result.has_database), ("IaC", result.has_iac)) if v]
    if chars:
        cprint(f"  Detected:     {', '.join(chars)}", "info")

    cprint("")
    for lbl, val in (("Build", result.build_cmd),
                     ("Test", result.test_cmd), ("Run", result.run_cmd)):
        if val:
            cprint(f"  {lbl}:{' ' * (6 - len(lbl))}{styled(val, 'muted')}")
    for lbl, seq in (("Tools", result.required_tools), ("Source", result.source_dirs),
                     ("Tests", result.test_dirs)):
        if seq:
            cprint(f"  {lbl}:{' ' * (6 - len(lbl))}{', '.join(seq)}", "muted")
    cprint(f"  [dim]{'─' * 50}[/]")


_APP_TYPE_MENU = (
    ("1", "console", "Console     CLI tool, no server deployment"),
    ("2", "web", "Web         Web app with Docker, CI/CD"))


def _prompt_app_type(result: AnalysisResult) -> AnalysisResult:
    from dark_factory.ui.cli_colors import cprint, styled  # noqa: PLC0415

    cprint("")
    cprint(styled("  Select app type:", "info"))
    cprint("")
    for num, _, label in _APP_TYPE_MENU:
        cprint(f"    {styled(f'[{num}]', 'info')} {label}")
    cprint("")
    try:
        choice = input("  Choice [1]: ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        choice = "1"
    strat = next((s for n, s, _ in _APP_TYPE_MENU if choice == n), "console")
    cprint(f"  {styled('✔', 'success')} App type: {styled(strat, 'success')}")
    return replace(result, detected_app_type=strat, confidence="high")


def confirm_or_override_analysis(result: AnalysisResult) -> AnalysisResult:
    """Allow interactive override of low-confidence results."""
    from dark_factory.ui.cli_colors import cprint, styled  # noqa: PLC0415

    if not sys.stdin.isatty():
        return result
    if result.confidence not in ("high", "medium"):
        cprint("  ! Low confidence — please select app type.", "warning")
        return _prompt_app_type(result)
    cprint("")
    cprint(f"  {styled('[Enter]', 'success')} Accept detected app type ({styled(result.detected_app_type, 'success')})")
    cprint(f"  {styled('[o]', 'muted')}     Override — choose a different app type")
    cprint("")
    try:
        choice = input("  Choice: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return result
    if choice not in ("o", "override") and result.detected_app_type in ("console", "web"):
        return result
    return _prompt_app_type(result)
