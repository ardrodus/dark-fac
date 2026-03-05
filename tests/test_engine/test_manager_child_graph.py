"""Tests for ManagerHandler._load_child_graph resolution logic.

Verifies that child graph file resolution handles:
- Built-in pipelines directory lookup (by filename)
- Stem-based fallback via discover_pipelines()
- Clear error messages for missing files
- Clear error messages for unexpanded variables
- Path traversal rejection
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from dark_factory.engine.handlers.manager import ManagerHandler


@pytest.fixture
def handler() -> ManagerHandler:
    return ManagerHandler()


@pytest.fixture
def tmp_dot(tmp_path: Path) -> Path:
    """Create a minimal DOT file in a temp directory."""
    dot = tmp_path / "test_child.dot"
    dot.write_text(
        textwrap.dedent("""\
        digraph test_child {
            graph [goal="child test"]
            start [shape=Mdiamond]
            done [shape=Msquare]
            start -> done
        }
        """),
        encoding="utf-8",
    )
    return dot


class TestLoadChildGraphResolution:
    """Test the 4-step resolution order for child graph files."""

    def test_absolute_path(self, handler: ManagerHandler, tmp_dot: Path) -> None:
        """Absolute paths resolve directly."""
        graph = handler._load_child_graph(str(tmp_dot), {"goal": "test"})
        assert graph.name == "test_child"

    def test_cwd_relative_path(self, handler: ManagerHandler, tmp_dot: Path) -> None:
        """CWD-relative paths resolve when the file exists."""
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_dot.parent)
            graph = handler._load_child_graph(tmp_dot.name, {"goal": "test"})
            assert graph.name == "test_child"
        finally:
            os.chdir(old_cwd)

    def test_builtin_dir_by_filename(self, handler: ManagerHandler) -> None:
        """Files are found in _BUILTINS_DIR by filename (strip directory prefix)."""
        # This simulates the real use case: child_graph="dark_factory/pipelines/arch_review_console.dot"
        # The CWD-relative path won't exist, but _BUILTINS_DIR / "arch_review_console.dot" should.
        from dark_factory.pipeline.loader import _BUILTINS_DIR

        candidate = _BUILTINS_DIR / "arch_review_console.dot"
        if not candidate.exists():
            pytest.skip("arch_review_console.dot not in builtins dir")

        graph = handler._load_child_graph(
            "some/nonexistent/path/arch_review_console.dot",
            {"goal": "test"},
        )
        assert "arch_review" in graph.name

    def test_discover_pipelines_stem_fallback(self, handler: ManagerHandler) -> None:
        """When no file path resolves, fall back to discover_pipelines() by stem."""
        from dark_factory.pipeline.loader import _BUILTINS_DIR

        candidate = _BUILTINS_DIR / "arch_review_console.dot"
        if not candidate.exists():
            pytest.skip("arch_review_console.dot not in builtins dir")

        # Use a path whose directory doesn't match any resolution step,
        # but whose stem (arch_review_console) matches a discovered pipeline.
        graph = handler._load_child_graph(
            "completely/bogus/arch_review_console.dot",
            {"goal": "test"},
        )
        assert "arch_review" in graph.name

    def test_strategy_expanded_path(self, handler: ManagerHandler) -> None:
        """After variable expansion, arch_review_${app_type}.dot resolves correctly."""
        from dark_factory.engine.variable_expansion import expand_variables
        from dark_factory.pipeline.loader import _BUILTINS_DIR

        candidate = _BUILTINS_DIR / "arch_review_console.dot"
        if not candidate.exists():
            pytest.skip("arch_review_console.dot not in builtins dir")

        source = "dark_factory/pipelines/arch_review_${app_type}.dot"
        context = {"app_type": "console", "goal": "test"}
        expanded = expand_variables(source, context, undefined="keep")

        graph = handler._load_child_graph(expanded, context)
        assert "arch_review" in graph.name


class TestLoadChildGraphErrors:
    """Test error handling for unresolvable child graphs."""

    def test_nonexistent_file_raises_file_not_found(self, handler: ManagerHandler) -> None:
        """A .dot path that can't be found raises FileNotFoundError, not ParseError."""
        with pytest.raises(FileNotFoundError, match="Child graph not found"):
            handler._load_child_graph("no_such_pipeline.dot", {"goal": "test"})

    def test_unexpanded_variable_raises_file_not_found(self, handler: ManagerHandler) -> None:
        """An unexpanded ${app_type} produces a clear FileNotFoundError."""
        source = "dark_factory/pipelines/arch_review_${app_type}.dot"
        with pytest.raises(FileNotFoundError, match="app_type.*variable"):
            handler._load_child_graph(source, {"goal": "test"})

    def test_path_traversal_rejected(self, handler: ManagerHandler) -> None:
        """Path traversal in child_graph source is rejected."""
        with pytest.raises(ValueError, match="Path traversal"):
            handler._load_child_graph("../../etc/passwd.dot", {"goal": "test"})


class TestLoadChildGraphInlineDot:
    """Test inline DOT source (non-.dot-suffix strings)."""

    def test_inline_dot_source(self, handler: ManagerHandler) -> None:
        """Non-.dot strings are treated as inline DOT source."""
        inline = textwrap.dedent("""\
        digraph inline_test {
            graph [goal="inline"]
            start [shape=Mdiamond]
            done [shape=Msquare]
            start -> done
        }
        """)
        graph = handler._load_child_graph(inline, {"goal": "test"})
        assert graph.name == "inline_test"

    def test_inline_dot_with_goal_expansion(self, handler: ManagerHandler) -> None:
        """$goal in inline DOT is expanded from context."""
        inline = textwrap.dedent("""\
        digraph goal_test {
            graph [goal="$goal"]
            start [shape=Mdiamond]
            done [shape=Msquare]
            start -> done
        }
        """)
        graph = handler._load_child_graph(inline, {"goal": "my goal"})
        assert graph.goal == "my goal"
