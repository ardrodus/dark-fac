"""Progress bars and spinners for long-running Dark Factory operations.

Wraps Rich's ``Progress`` and ``Status`` (spinner) for consistent styling
across pipeline stages, security scans, and Crucible runs.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import Any

from factory.ui.theme import PILLARS, THEME


@contextlib.contextmanager
def pipeline_progress(total_stages: int) -> Generator[Any, None, None]:
    """Context manager showing a Rich progress bar for pipeline stages."""
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    columns = [
        SpinnerColumn(style=f"bold {PILLARS.dark_forge}"),
        TextColumn("[bold]{task.description}"),
        BarColumn(
            complete_style=f"bold {THEME.success}",
            finished_style=f"bold {THEME.success}",
            bar_width=30,
        ),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]
    progress = Progress(*columns)
    task_id = progress.add_task("Pipeline", total=total_stages)
    progress._task_id = task_id  # type: ignore[attr-defined]
    with progress:
        yield progress


def advance_stage(progress: Any, description: str = "") -> None:
    """Advance the pipeline progress bar by one step."""
    task_id = progress._task_id  # type: ignore[attr-defined]
    if description:
        progress.update(task_id, description=description)
    progress.advance(task_id)


@contextlib.contextmanager
def spinner(message: str, *, pillar: str = "ouroboros") -> Generator[Any, None, None]:
    """Context manager showing a Rich spinner for a long-running operation."""
    from rich.console import Console

    pillar_colors = {
        "sentinel": PILLARS.sentinel,
        "dark_forge": PILLARS.dark_forge,
        "crucible": PILLARS.crucible,
        "obelisk": PILLARS.obelisk,
        "ouroboros": PILLARS.ouroboros,
    }
    colour = pillar_colors.get(pillar, PILLARS.ouroboros)
    console = Console()
    with console.status(
        f"[bold {colour}]{message}[/]",
        spinner="dots",
        spinner_style=f"bold {colour}",
    ) as status:
        yield status
