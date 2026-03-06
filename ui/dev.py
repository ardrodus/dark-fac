"""Dev entry point for ``textual run --dev dark_factory.ui.dev:app``.

Lightweight convenience for hot-reloading the dashboard during development.
No impact on production paths.
"""

from dark_factory.ui.dashboard import DashboardApp

app = DashboardApp()
