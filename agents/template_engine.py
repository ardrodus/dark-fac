"""Agent Template Engine — resolves agent definitions from base + strategy overlay.

Reads a Jinja2 base template from ``factory/agents/templates/<role>.md`` and
merges strategy-specific variable values from
``factory/agents/overlays/<strategy>.yaml``.  Results are cached per session
via :func:`functools.lru_cache`.
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
from pathlib import Path

import yaml
from jinja2 import BaseLoader, Environment, Undefined

# ── Paths ─────────────────────────────────────────────────────────

_AGENTS_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _AGENTS_DIR / "templates"
_OVERLAYS_DIR = _AGENTS_DIR / "overlays"

# ── Silent-undefined for stripping unresolved placeholders ────────


class _StripUndefined(Undefined):
    """Return empty string for any undefined variable."""

    def __str__(self) -> str:
        return ""

    def __iter__(self) -> _StripUndefined:
        return self

    def __bool__(self) -> bool:
        return False


_ENV = Environment(
    loader=BaseLoader(),
    undefined=_StripUndefined,
    keep_trailing_newline=True,
)

# Regex used to clean leftover empty-placeholder artifacts after rendering.
_LEFTOVER_RE = re.compile(r"[ \t]*\n(?=[ \t]*\n)")

# Regex to extract Jinja2 placeholder names from template text.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


# ── Helpers ───────────────────────────────────────────────────────


def _read_template(role: str) -> str:
    """Read the base template file for *role*.

    Raises
    ------
    FileNotFoundError
        If no template exists for the given role.
    """
    path = _TEMPLATES_DIR / f"{role}.md"
    return path.read_text(encoding="utf-8")


def _read_overlay(strategy: str) -> dict[str, dict[str, str]]:
    """Read and parse the strategy overlay YAML.

    Returns a mapping of ``{role: {variable: value, ...}, ...}``.
    Returns an empty dict if no overlay file exists for *strategy*.
    """
    path = _OVERLAYS_DIR / f"{strategy}.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for key, val in raw.items():
        if isinstance(val, dict):
            result[str(key)] = {str(k): str(v) for k, v in val.items()}
    return result


def _render(template_text: str, variables: dict[str, str]) -> str:
    """Render *template_text* with Jinja2, substituting *variables*.

    Undefined placeholders are silently stripped.  Consecutive blank
    lines left after stripping are collapsed.
    """
    tpl = _ENV.from_string(template_text)
    rendered = tpl.render(variables)
    # Collapse runs of blank lines left by stripped placeholders.
    return _LEFTOVER_RE.sub("\n", rendered)


# ── Public API ────────────────────────────────────────────────────


@functools.lru_cache(maxsize=128)
def resolve_agent_definition(role: str, strategy: str) -> str:
    """Resolve the full agent definition for *role* under *strategy*.

    1. Reads ``factory/agents/templates/<role>.md`` (base template).
    2. Loads ``factory/agents/overlays/<strategy>.yaml`` and extracts
       the variables for *role*.
    3. Renders the template with Jinja2, filling placeholders.
    4. If no overlay exists for the role+strategy combination the base
       definition is returned with unresolved placeholders stripped.

    Parameters
    ----------
    role:
        Agent role name (e.g. ``"pipeline"``, ``"tdd"``).
    strategy:
        Strategy identifier (e.g. ``"aggressive"``, ``"conservative"``).

    Returns
    -------
    str
        The fully resolved agent definition text.

    Raises
    ------
    FileNotFoundError
        If the base template for *role* does not exist.
    """
    base = _read_template(role)
    overlay = _read_overlay(strategy)
    variables = overlay.get(role, {})
    return _render(base, variables)


def clear_cache() -> None:
    """Clear the template resolution cache."""
    resolve_agent_definition.cache_clear()


def _extract_placeholders(template_text: str) -> set[str]:
    """Extract all Jinja2 placeholder names from *template_text*."""
    return set(_PLACEHOLDER_RE.findall(template_text))


def validate_overlays() -> list[str]:
    """Check that every overlay provides values for all placeholders it covers.

    For each overlay file, for each role defined in that overlay, verify
    that all placeholders in the corresponding template have a value.

    Returns a list of error strings (empty means all valid).
    """
    errors: list[str] = []
    overlay_files = sorted(_OVERLAYS_DIR.glob("*.yaml"))
    template_files = sorted(_TEMPLATES_DIR.glob("*.md"))

    role_placeholders: dict[str, set[str]] = {}
    for tpl_path in template_files:
        role = tpl_path.stem
        text = tpl_path.read_text(encoding="utf-8")
        role_placeholders[role] = _extract_placeholders(text)

    for ovl_path in overlay_files:
        strategy = ovl_path.stem
        overlay = _read_overlay(strategy)
        for role, variables in overlay.items():
            if role not in role_placeholders:
                errors.append(
                    f"{strategy}: role '{role}' has no template"
                )
                continue
            missing = role_placeholders[role] - set(variables.keys())
            if missing:
                errors.append(
                    f"{strategy}/{role}: missing placeholders: "
                    f"{', '.join(sorted(missing))}"
                )
    return errors


def _main() -> None:
    """CLI entry point for ``python -m factory.agents.template_engine``."""
    parser = argparse.ArgumentParser(
        description="Agent template engine utilities.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate all overlay files provide values for template placeholders.",
    )
    args = parser.parse_args()

    if args.validate:
        errors = validate_overlays()
        if errors:
            print("Validation FAILED:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)
        print("All overlays valid.")
        sys.exit(0)

    parser.print_help()


if __name__ == "__main__":
    _main()
