"""Threshold and RAG classification.

The API owns `days_open` and `past_threshold`; this module owns only how they are displayed.
`days_open == threshold` is past threshold — that boundary is the thing most likely to be
quietly wrong, so it is asserted directly in `tests/unit/test_thresholds.py`.
"""

from __future__ import annotations

from enum import StrEnum
from math import ceil


class Tone(StrEnum):
    RED = "red"
    AMBER = "amber"
    GREEN = "green"
    GREY = "grey"


def is_past_threshold(days_open: int, threshold_days: int) -> bool:
    """True when an incident has reached the contact threshold (inclusive)."""
    return days_open >= threshold_days


def days_tone(days_open: int, threshold_days: int) -> Tone:
    """Red at or past the threshold, amber from halfway, grey below that."""
    if days_open >= threshold_days:
        return Tone.RED
    if days_open >= ceil(threshold_days / 2):
        return Tone.AMBER
    return Tone.GREY


def days_label(days_open: int) -> str:
    return f"{days_open}d"


_STATUS_TONES = {
    "contact_due": Tone.RED,
    "not_contacted": Tone.RED,
    "overdue": Tone.RED,
    "awaiting_reply": Tone.AMBER,
    "contacted": Tone.AMBER,
    "monitoring": Tone.AMBER,
    "resolved": Tone.GREEN,
    "on_target": Tone.GREEN,
    "new": Tone.GREY,
}

_STATUS_LABELS = {
    "contact_due": "Contact due",
    "not_contacted": "Not contacted",
    "overdue": "Overdue",
    "awaiting_reply": "Awaiting reply",
    "contacted": "Contacted",
    "monitoring": "Monitoring",
    "resolved": "Resolved",
    "on_target": "On target",
    "new": "New",
}


def status_label(status: str) -> str:
    """Human label for an API status token, falling back to a de-slugged form."""
    return _STATUS_LABELS.get(status, status.replace("_", " ").capitalize() or "Unknown")


def status_tone(status: str) -> Tone:
    return _STATUS_TONES.get(status, Tone.GREY)


def score_tone(score: float | None) -> Tone:
    """Coverage and quality percentages: on target at 80, watch from 60."""
    if score is None:
        return Tone.GREY
    if score >= 80:
        return Tone.GREEN
    if score >= 60:
        return Tone.AMBER
    return Tone.RED
