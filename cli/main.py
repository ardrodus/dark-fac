"""Dark Factory CLI — thin entry-point dispatcher.

Responsibilities (and nothing else):
  1. Module loader initialisation
  2. CLI argument parsing
  3. Command dispatch
  4. Top-level exception handling

All command logic lives in :mod:`factory.cli.handlers`.
All Click commands live in :mod:`factory.cli.commands`.
All dispatch wiring lives in :mod:`factory.cli.dispatch`.
"""

from __future__ import annotations

import sys

from dark_factory.cli.commands import cli as cli  # noqa: F401
from dark_factory.cli.dispatch import dispatch_config as _dispatch_config  # noqa: F401
from dark_factory.cli.dispatch import dispatch_doctor as _dispatch_doctor  # noqa: F401
from dark_factory.cli.dispatch import dispatch_onboard as _dispatch_onboard  # noqa: F401
from dark_factory.cli.dispatch import dispatch_selftest as _dispatch_selftest  # noqa: F401
from dark_factory.cli.dispatch import dispatch_smoke_test as _dispatch_smoke_test  # noqa: F401
from dark_factory.cli.dispatch import dispatch_workspace as _dispatch_workspace  # noqa: F401


def main() -> None:
    """CLI entry point: parse arguments and dispatch.

    This is the ``dark-factory`` console-script target registered in
    ``pyproject.toml`` (``factory.cli.main:main``).
    """
    from dark_factory.cli.dispatch import dispatch
    from dark_factory.cli.parser import parse_cli_args

    try:
        parsed = parse_cli_args(sys.argv[1:])
        dispatch(parsed)
    except KeyboardInterrupt:
        sys.stderr.write("\n\033[33mInterrupted.\033[0m\n")
        raise SystemExit(130) from None
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        from dark_factory.ui.cli_colors import print_error  # noqa: PLC0415

        print_error(str(exc), hint="Run 'dark-factory doctor' to diagnose issues.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
