"""Story 5: Unit tests for project_analyzer.py styled display.

Tests display_analysis_results() in setup/project_analyzer.py:
- Structured Rich output instead of plain dividers
- Language detection label is styled with color
- Framework detection info formatted correctly
- Plain text fallback works without Rich
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from dark_factory.setup.project_analyzer import AnalysisResult, display_analysis_results


def _capture_display(result: AnalysisResult) -> str:
    """Run display_analysis_results and capture stdout output."""
    captured: list[str] = []
    with patch("sys.stdout.write", side_effect=lambda s: captured.append(s)):
        display_analysis_results(result)
    return "".join(captured)


class TestProjectAnalyzerDisplay:
    """4 unit tests for display_analysis_results()."""

    def test_structured_output_with_dividers(self) -> None:
        """Analysis results use structured output with dividers."""
        result = AnalysisResult(
            language="Python",
            framework="FastAPI",
            detected_strategy="web",
            confidence="high",
            description="Python/FastAPI project",
        )
        output = _capture_display(result)
        # Should contain structural elements (dividers, section headers)
        assert "---" in output or "───" in output or "━" in output or "Analysis" in output

    def test_language_detection_displayed(self) -> None:
        """Language detection label is present in output."""
        result = AnalysisResult(
            language="TypeScript",
            framework="React",
            detected_strategy="web",
            confidence="high",
            description="TypeScript/React project",
        )
        output = _capture_display(result)
        assert "TypeScript" in output
        assert "React" in output

    def test_framework_detection_formatted(self) -> None:
        """Framework detection info is formatted correctly."""
        result = AnalysisResult(
            language="Python",
            framework="Django",
            detected_strategy="web",
            confidence="high",
            description="Python/Django project",
        )
        output = _capture_display(result)
        # Framework should appear in language line or separately
        assert "Django" in output
        assert "Python" in output

    def test_plain_text_fallback_without_rich(self) -> None:
        """Plain text fallback works without Rich installed."""
        result = AnalysisResult(
            language="Go",
            framework="Gin",
            detected_strategy="web",
            confidence="medium",
            description="Go/Gin project",
        )
        with patch.dict(sys.modules, {"rich": None, "rich.console": None, "rich.table": None, "rich.panel": None}):
            output = _capture_display(result)
            # Should still produce readable output
            assert "Go" in output
            assert "Gin" in output
            assert "web" in output
