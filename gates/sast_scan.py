"""SAST gate — bridge to :mod:`factory.security.sast_scan`.

Exposes the discovery protocol (``GATE_NAME`` + ``create_runner``) so the
gate framework auto-discovers this gate alongside the others.
"""

from factory.security.sast_scan import GATE_NAME, create_runner

__all__ = ["GATE_NAME", "create_runner"]
