"""Shared test fixtures for ported engine tests.

No external dependency stubs are needed — all engine types are defined
locally in factory.engine.types.
"""

# Skip test files that reference non-ported attractor features.
collect_ignore = [
    "test_wave2_system_prompts.py",  # needs _walk_path (not ported)
    "test_wave8_interactive_subagent.py",  # needs TrackedSubagent (not ported)
]
