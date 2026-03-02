"""Project documentation discovery for agent system prompts.

Replaces attractor_agent.project_docs with a lightweight implementation
that discovers CLAUDE.md and similar project instruction files.
"""

from __future__ import annotations

from pathlib import Path


def discover_project_docs(
    *,
    working_dir: str | None = None,
    provider_id: str | None = None,  # noqa: ARG001
    git_root: str | None = None,
) -> str:
    """Discover and concatenate project documentation for the system prompt.

    Searches for CLAUDE.md, .claude/instructions.md, and similar files
    in the working directory and git root.

    Returns concatenated content, or empty string if nothing found.
    """
    search_dirs: list[Path] = []
    if working_dir:
        search_dirs.append(Path(working_dir))
    if git_root and git_root != working_dir:
        search_dirs.append(Path(git_root))

    doc_files = [
        "CLAUDE.md",
        ".claude/instructions.md",
    ]

    parts: list[str] = []
    seen: set[str] = set()
    for d in search_dirs:
        for name in doc_files:
            p = d / name
            if p.is_file():
                resolved = str(p.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    content = p.read_text(encoding="utf-8").strip()
                    if content:
                        parts.append(
                            f"<project_instructions source=\"{name}\">\n"
                            f"{content}\n"
                            f"</project_instructions>"
                        )
                except OSError:
                    pass

    return "\n\n".join(parts)
