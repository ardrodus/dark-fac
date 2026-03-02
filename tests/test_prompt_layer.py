"""Tests for factory.engine.agent.prompt_layer -- 4-layer prompt composition.

Covers:
- Layer 1: System preamble (agent protocol + provider profile)
- Layer 2: Agent role (.md file loading)
- Layer 3: Node prompt
- Layer 4: Context variables (goal, context, resume preamble)
- Epilogue from agent protocol
- User override
- Backward compatibility (no agent_type)
- load_role_definition helper
- layer_prompt_for_node convenience wrapper
"""

from __future__ import annotations

from pathlib import Path

from dark_factory.engine.agent.prompt_layer import (
    PromptLayer,
    build_system_prompt,
    layer_prompt_for_node,
    load_role_definition,
)

# ── load_role_definition ──────────────────────────────────────────


class TestLoadRoleDefinition:
    """Tests for loading .md role definition files."""

    def test_loads_existing_md_file(self, tmp_path: Path) -> None:
        md = tmp_path / "test-role.md"
        md.write_text("# Test Role\n\nYou are a test agent.", encoding="utf-8")
        result = load_role_definition("test-role", search_dir=tmp_path)
        assert result == "# Test Role\n\nYou are a test agent."

    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        result = load_role_definition("nonexistent", search_dir=tmp_path)
        assert result == ""

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        md = tmp_path / "padded.md"
        md.write_text("  \n  content  \n  ", encoding="utf-8")
        result = load_role_definition("padded", search_dir=tmp_path)
        assert result == "content"

    def test_loads_real_agent_md_file(self) -> None:
        """Smoke test: loads one of the real sa-*.md files."""
        result = load_role_definition("sa-code-quality")
        assert "Code Quality" in result

    def test_default_search_dir(self) -> None:
        """Default dir points to factory/agents/."""
        # sa-security-web.md exists in the real codebase
        result = load_role_definition("sa-security-web")
        assert "Security" in result


# ── build_system_prompt: Layer 1 (preamble) ──────────────────────


class TestLayer1Preamble:
    """Layer 1: system preamble from agent protocol."""

    def test_includes_protocol_preamble_when_agent_type_set(self) -> None:
        result = build_system_prompt(agent_type="sa-code-quality")
        assert "Agent Protocol" in result
        assert "Pre-Work Context Loading" in result

    def test_includes_profile_prompt(self) -> None:
        result = build_system_prompt(profile_prompt="You are an expert engineer.")
        assert "You are an expert engineer." in result

    def test_preamble_before_profile(self) -> None:
        result = build_system_prompt(
            agent_type="test-writer",
            profile_prompt="PROFILE_TEXT",
        )
        preamble_pos = result.find("Agent Protocol")
        profile_pos = result.find("PROFILE_TEXT")
        assert preamble_pos < profile_pos

    def test_no_preamble_without_agent_type(self) -> None:
        result = build_system_prompt(profile_prompt="base prompt")
        assert "Agent Protocol" not in result

    def test_no_preamble_when_include_protocol_false(self) -> None:
        result = build_system_prompt(
            agent_type="sa-code-quality",
            include_protocol=False,
        )
        assert "Agent Protocol" not in result


# ── build_system_prompt: Layer 2 (agent role) ────────────────────


class TestLayer2Role:
    """Layer 2: agent role from .md files."""

    def test_loads_role_from_md_file(self) -> None:
        result = build_system_prompt(agent_type="sa-code-quality")
        assert "[ROLE]" in result
        assert "Code Quality" in result

    def test_explicit_role_definition_overrides_md(self) -> None:
        result = build_system_prompt(
            agent_type="sa-code-quality",
            role_definition="Custom role text",
        )
        assert "[ROLE]" in result
        assert "Custom role text" in result
        # Should NOT contain the md file content
        assert "Code architecture" not in result

    def test_empty_role_definition_skips_section(self) -> None:
        result = build_system_prompt(
            agent_type="nonexistent-role",
            include_protocol=False,
        )
        assert "[ROLE]" not in result

    def test_role_after_preamble(self) -> None:
        result = build_system_prompt(agent_type="sa-code-quality")
        preamble_pos = result.find("Agent Protocol")
        role_pos = result.find("[ROLE]")
        assert preamble_pos < role_pos


# ── build_system_prompt: Layer 3 (node prompt) ───────────────────


class TestLayer3NodePrompt:
    """Layer 3: node-specific instructions."""

    def test_includes_node_instruction(self) -> None:
        result = build_system_prompt(node_instruction="Focus on error handling")
        assert "[INSTRUCTION] Focus on error handling" in result

    def test_no_instruction_section_when_empty(self) -> None:
        result = build_system_prompt(profile_prompt="base")
        assert "[INSTRUCTION]" not in result

    def test_instruction_after_role(self) -> None:
        result = build_system_prompt(
            agent_type="sa-code-quality",
            node_instruction="Do the thing",
        )
        role_pos = result.find("[ROLE]")
        instr_pos = result.find("[INSTRUCTION]")
        assert role_pos < instr_pos


# ── build_system_prompt: Layer 4 (context) ───────────────────────


class TestLayer4Context:
    """Layer 4: pipeline goal, context variables, resume preamble."""

    def test_includes_pipeline_goal(self) -> None:
        result = build_system_prompt(pipeline_goal="Build a REST API")
        assert "[GOAL] Build a REST API" in result

    def test_includes_context_variables(self) -> None:
        result = build_system_prompt(
            pipeline_context={"repo": "my-app", "branch": "main"},
        )
        assert "[CONTEXT]" in result
        assert "repo: my-app" in result
        assert "branch: main" in result

    def test_filters_internal_context_keys(self) -> None:
        result = build_system_prompt(
            pipeline_context={
                "visible": "yes",
                "_internal": "hidden",
                "parallel.count": 4,
                "manager.state": "running",
                "codergen.output": "text",
            },
        )
        assert "visible: yes" in result
        assert "_internal" not in result
        assert "parallel.count" not in result
        assert "manager.state" not in result
        assert "codergen.output" not in result

    def test_explicit_resume_preamble(self) -> None:
        result = build_system_prompt(
            resume_preamble="[RESUME] Resuming from checkpoint...",
        )
        assert "[RESUME] Resuming from checkpoint..." in result

    def test_resume_preamble_from_pipeline_context(self) -> None:
        result = build_system_prompt(
            pipeline_context={"_resume_preamble": "[RESUME] From context"},
        )
        assert "[RESUME] From context" in result

    def test_explicit_resume_takes_precedence(self) -> None:
        result = build_system_prompt(
            resume_preamble="EXPLICIT",
            pipeline_context={"_resume_preamble": "FROM_CONTEXT"},
        )
        assert "EXPLICIT" in result
        # Should not double-include
        assert result.count("EXPLICIT") == 1

    def test_goal_after_instruction(self) -> None:
        result = build_system_prompt(
            node_instruction="Do stuff",
            pipeline_goal="The goal",
            include_protocol=False,
        )
        instr_pos = result.find("[INSTRUCTION]")
        goal_pos = result.find("[GOAL]")
        assert instr_pos < goal_pos


# ── build_system_prompt: Epilogue ─────────────────────────────────


class TestEpilogue:
    """Epilogue from agent protocol."""

    def test_includes_epilogue_with_agent_type(self) -> None:
        result = build_system_prompt(agent_type="sa-code-quality")
        assert "Post-Work Knowledge Capture" in result

    def test_epilogue_at_end(self) -> None:
        result = build_system_prompt(
            agent_type="sa-code-quality",
            node_instruction="Middle content",
        )
        epilogue_pos = result.find("Post-Work Knowledge Capture")
        instr_pos = result.find("[INSTRUCTION]")
        assert epilogue_pos > instr_pos

    def test_no_epilogue_without_agent_type(self) -> None:
        result = build_system_prompt(profile_prompt="base")
        assert "Post-Work Knowledge Capture" not in result

    def test_no_epilogue_when_include_protocol_false(self) -> None:
        result = build_system_prompt(
            agent_type="sa-code-quality",
            include_protocol=False,
        )
        assert "Post-Work Knowledge Capture" not in result


# ── build_system_prompt: User override ────────────────────────────


class TestUserOverride:
    """User override replaces entire prompt."""

    def test_user_override_replaces_all(self) -> None:
        result = build_system_prompt(
            agent_type="sa-code-quality",
            profile_prompt="provider base",
            pipeline_goal="goal",
            node_instruction="instruction",
            user_override="CUSTOM OVERRIDE",
        )
        assert result == "CUSTOM OVERRIDE"

    def test_blank_override_ignored(self) -> None:
        result = build_system_prompt(
            profile_prompt="base",
            user_override="   ",
        )
        assert "base" in result

    def test_none_override_ignored(self) -> None:
        result = build_system_prompt(
            profile_prompt="base",
            user_override=None,
        )
        assert "base" in result


# ── build_system_prompt: Full 4-layer composition ────────────────


class TestFull4LayerComposition:
    """Integration: all 4 layers compose in correct order."""

    def test_all_layers_present(self) -> None:
        result = build_system_prompt(
            agent_type="sa-code-quality",
            profile_prompt="PROFILE",
            node_instruction="NODE_INSTR",
            pipeline_goal="THE_GOAL",
            pipeline_context={"key": "val"},
        )
        # Layer 1
        assert "Agent Protocol" in result
        assert "PROFILE" in result
        # Layer 2
        assert "[ROLE]" in result
        assert "Code Quality" in result
        # Layer 3
        assert "[INSTRUCTION] NODE_INSTR" in result
        # Layer 4
        assert "[GOAL] THE_GOAL" in result
        assert "key: val" in result
        # Epilogue
        assert "Post-Work Knowledge Capture" in result

    def test_layer_ordering(self) -> None:
        result = build_system_prompt(
            agent_type="sa-code-quality",
            profile_prompt="PROFILE_MARKER",
            node_instruction="NODE_MARKER",
            pipeline_goal="GOAL_MARKER",
        )
        preamble_pos = result.find("Agent Protocol")
        profile_pos = result.find("PROFILE_MARKER")
        role_pos = result.find("[ROLE]")
        node_pos = result.find("[INSTRUCTION]")
        goal_pos = result.find("[GOAL]")
        epilogue_pos = result.find("Post-Work Knowledge Capture")

        assert preamble_pos < profile_pos < role_pos < node_pos < goal_pos < epilogue_pos


# ── Backward compatibility ────────────────────────────────────────


class TestBackwardCompat:
    """Existing callers without agent_type still work."""

    def test_basic_composition_without_agent_type(self) -> None:
        result = build_system_prompt(
            profile_prompt="You are helpful.",
            pipeline_goal="Build something",
            node_instruction="Be careful",
        )
        assert "You are helpful." in result
        assert "[GOAL] Build something" in result
        assert "[INSTRUCTION] Be careful" in result
        # No protocol artifacts
        assert "Agent Protocol" not in result

    def test_empty_call_returns_empty(self) -> None:
        result = build_system_prompt()
        assert result == ""


# ── layer_prompt_for_node ─────────────────────────────────────────


class TestLayerPromptForNode:
    """Convenience wrapper tests."""

    def test_delegates_to_build_system_prompt(self) -> None:
        result = layer_prompt_for_node(
            profile_prompt="PROFILE",
            goal="GOAL",
            node_system_prompt="NODE",
        )
        assert "PROFILE" in result
        assert "[GOAL] GOAL" in result
        assert "[INSTRUCTION] NODE" in result

    def test_user_system_prompt_overrides(self) -> None:
        result = layer_prompt_for_node(
            profile_prompt="base",
            user_system_prompt="OVERRIDE",
        )
        assert result == "OVERRIDE"

    def test_passes_agent_type(self) -> None:
        result = layer_prompt_for_node(
            agent_type="sa-code-quality",
            goal="test",
        )
        assert "Agent Protocol" in result
        assert "[ROLE]" in result

    def test_passes_resume_preamble(self) -> None:
        result = layer_prompt_for_node(
            resume_preamble="[RESUME] test checkpoint",
        )
        assert "[RESUME] test checkpoint" in result

    def test_passes_task_context(self) -> None:
        result = layer_prompt_for_node(
            agent_type="test-writer",
            task_context={"task_description": "Write tests"},
        )
        assert "Write tests" in result


# ── PromptLayer dataclass ─────────────────────────────────────────


class TestPromptLayerDataclass:
    """PromptLayer dataclass preserved for API stability."""

    def test_creation(self) -> None:
        layer = PromptLayer(source="role", content="text")
        assert layer.source == "role"
        assert layer.content == "text"
        assert layer.mode == "append"

    def test_replace_mode(self) -> None:
        layer = PromptLayer(source="node", content="x", mode="replace")
        assert layer.mode == "replace"
