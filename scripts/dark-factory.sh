#!/usr/bin/env bash
# dark-factory.sh — entry point during the bash→Python migration.
#
# Delegates to Python modules as they are rewritten; falls back to bash
# implementations for modules not yet migrated.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FACTORY_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Migration bridge ────────────────────────────────────────────────

delegate_to_python() {
    # Delegate a command to the equivalent Python module.
    #
    # Usage: delegate_to_python <module> [args...]
    #   module — dotted Python module path, e.g. factory.cli.main
    #   args   — forwarded to the module as sys.argv
    #
    # Returns the exit code from the Python process.
    local module="$1"
    shift
    local exit_code=0
    python3 -m "$module" "$@" || exit_code=$?
    return "$exit_code"
}

# ── Subcommand routing ──────────────────────────────────────────────

main() {
    local cmd="${1:-help}"
    shift 2>/dev/null || true

    case "$cmd" in
        doctor)
            # doctor is migrated to Python
            delegate_to_python factory.cli.main doctor "$@"
            ;;
        version|--version)
            delegate_to_python factory.cli.main --version
            ;;
        help|--help|-h)
            echo "Usage: dark-factory <command> [options]"
            echo ""
            echo "Commands:"
            echo "  doctor     Run system health checks"
            echo "  version    Show version information"
            echo "  help       Show this help message"
            ;;
        *)
            echo "Unknown command: $cmd" >&2
            echo "Run 'dark-factory help' for usage information." >&2
            exit 1
            ;;
    esac
}

# Only run main when executed, not when sourced (for testing).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
