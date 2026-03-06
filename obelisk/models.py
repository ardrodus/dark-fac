"""Obelisk data contracts — immutable models for alerts and investigations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Alert:
    """An alert raised by a pipeline node.

    Pure data contract — no display logic or heuristics.
    """

    error_type: str
    source: str
    pipeline: str
    node: str
    message: str
    signature: str


@dataclass(frozen=True, slots=True)
class Investigation:
    """Result of investigating an alert.

    Pure data contract — no display logic or heuristics.
    """

    id: str
    alert: Alert
    verdict: str
    outcome_url: str
    duration_s: float
