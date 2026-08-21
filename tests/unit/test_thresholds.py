"""Threshold arithmetic — the logic most likely to be quietly wrong.

The API owns `days_open`; what is tested here is the classification the dashboard applies
to it, above all the inclusive boundary at `days_open == threshold`.
"""

from __future__ import annotations

import pytest

from stewards.monitors.thresholds import (
    Tone,
    days_label,
    days_tone,
    is_past_threshold,
    score_tone,
    status_label,
    status_tone,
)


@pytest.mark.parametrize(
    ("days_open", "threshold", "expected"),
    [
        (0, 7, False),
        (1, 7, False),
        (6, 7, False),
        (7, 7, True),  # the boundary: at the threshold IS past it
        (8, 7, True),
        (38, 7, True),
        (1, 1, True),
        (0, 1, False),
        (13, 14, False),
        (14, 14, True),
    ],
)
def test_past_threshold_boundary(days_open: int, threshold: int, expected: bool) -> None:
    assert is_past_threshold(days_open, threshold) is expected


@pytest.mark.parametrize(
    ("days_open", "threshold", "expected"),
    [
        (0, 7, Tone.GREY),
        (3, 7, Tone.GREY),
        (4, 7, Tone.AMBER),  # halfway of 7 rounds up to 4
        (6, 7, Tone.AMBER),
        (7, 7, Tone.RED),
        (22, 7, Tone.RED),
        (1, 2, Tone.AMBER),
        (2, 2, Tone.RED),
        (5, 14, Tone.GREY),
        (7, 14, Tone.AMBER),
    ],
)
def test_days_tone(days_open: int, threshold: int, expected: Tone) -> None:
    assert days_tone(days_open, threshold) is expected


def test_days_tone_agrees_with_past_threshold_at_every_boundary() -> None:
    for threshold in range(1, 31):
        for days in range(0, threshold + 2):
            assert (days_tone(days, threshold) is Tone.RED) == is_past_threshold(
                days, threshold
            )


def test_days_label() -> None:
    assert days_label(0) == "0d"
    assert days_label(22) == "22d"


@pytest.mark.parametrize(
    ("status", "tone", "label"),
    [
        ("contact_due", Tone.RED, "Contact due"),
        ("awaiting_reply", Tone.AMBER, "Awaiting reply"),
        ("monitoring", Tone.AMBER, "Monitoring"),
        ("new", Tone.GREY, "New"),
        ("resolved", Tone.GREEN, "Resolved"),
    ],
)
def test_known_statuses(status: str, tone: Tone, label: str) -> None:
    assert status_tone(status) is tone
    assert status_label(status) == label


def test_unknown_status_degrades_to_grey_and_a_readable_label() -> None:
    assert status_tone("something_new_from_the_api") is Tone.GREY
    assert status_label("something_new_from_the_api") == "Something new from the api"


def test_empty_status_is_not_blank() -> None:
    assert status_label("") == "Unknown"


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (None, Tone.GREY),
        (0.0, Tone.RED),
        (59.9, Tone.RED),
        (60.0, Tone.AMBER),
        (79.9, Tone.AMBER),
        (80.0, Tone.GREEN),
        (100.0, Tone.GREEN),
    ],
)
def test_score_tone(score: float | None, expected: Tone) -> None:
    assert score_tone(score) is expected
