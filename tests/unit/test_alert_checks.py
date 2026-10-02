"""The alert rules: the Monday window, the feed-type filter, and every check's wiring."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from stewards.alerts.checks import CHECKS, Check, matching_values
from stewards.alerts.evaluate import Digest, select, uk_today
from stewards.api.models import Incident
from stewards.monitors.registry import get_monitor
from stewards.monitors.transforms import resolve_field

MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)
FRIDAY = date(2026, 10, 9)

STALL = Check(
    id="t", label="Stalls", monitor_id="single_feed_stall", field="days_open",
    field_label="Days stalled", threshold=10, feed_types=frozenset({"Slot"}),
)  # fmt: skip


def incident(days: int | None, feed_type: str | None = "Slot", **extra: Any) -> Incident:
    return Incident.model_validate(
        {
            "monitor_id": "single_feed_stall",
            "publisher_id": "p",
            "publisher_name": extra.pop("publisher_name", "Pub"),
            "past_threshold": False,
            "status": "open",
            "days_open": days,
            "feed_type": feed_type,
            "feed_name": "slots",
            "feed_url": "https://example.org/slots",
            **extra,
        }
    )


@pytest.mark.parametrize("today", [TUESDAY, FRIDAY])
def test_weekday_window_is_the_threshold_only(today: date) -> None:
    assert list(matching_values(5, today)) == [5]


def test_monday_window_covers_the_weekend() -> None:
    assert list(matching_values(5, MONDAY)) == [5, 6, 7]


@pytest.mark.parametrize(
    ("days", "today", "hit"),
    [
        (9, TUESDAY, False),
        (10, TUESDAY, True),
        (11, TUESDAY, False),
        (9, MONDAY, False),
        (12, MONDAY, True),
        (13, MONDAY, False),
        (None, TUESDAY, False),
    ],
)
def test_threshold_boundaries(days: int | None, today: date, hit: bool) -> None:
    assert bool(select(STALL, [incident(days)], today).hits) is hit


def test_feed_type_filter() -> None:
    result = select(STALL, [incident(10, "Slot"), incident(10, "FacilityUse")], TUESDAY)
    assert [h.feed_type for h in result.hits] == ["Slot"]


def test_no_feed_type_filter_accepts_any() -> None:
    check = Check("t", "x", "single_feed_stall", "days_open", "d", 10)
    assert len(select(check, [incident(10, None), incident(10, "X")], TUESDAY).hits) == 2


def test_empty_incidents() -> None:
    result = select(STALL, [], TUESDAY)
    assert result.hits == ()
    assert Digest(today=TUESDAY, results=(result,)).total == 0


def test_hits_are_resolved_and_sorted() -> None:
    result = select(
        STALL,
        [incident(10, publisher_name="beta"), incident(10, publisher_name="Alpha")],
        TUESDAY,
    )
    assert [h.publisher for h in result.hits] == ["Alpha", "beta"]
    hit = result.hits[0]
    assert (hit.subject, hit.value, hit.link) == ("slots", 10, "https://example.org/slots")


def test_uk_today_crosses_midnight_in_bst() -> None:
    assert uk_today(datetime(2026, 7, 1, 23, 30, tzinfo=UTC)) == date(2026, 7, 2)


def test_configured_rules() -> None:
    by_id = {c.id: c for c in CHECKS}
    assert by_id["dataset_stall"].threshold == 5
    assert by_id["single_feed_stall"].threshold == 10
    assert by_id["single_feed_stall"].feed_types == {"Slot", "ScheduledSession"}
    assert by_id["feed_ingestion_error"].threshold == 10
    assert len(by_id) == len(CHECKS)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.id)
def test_every_check_resolves_against_its_monitor(check: Check) -> None:
    monitor = get_monitor(check.monitor_id)
    row = Incident.model_validate(
        {
            "monitor_id": monitor.id,
            "publisher_id": "p",
            "publisher_name": "P",
            "past_threshold": False,
            "status": "open",
            "days_open": check.threshold,
        }
    )
    assert resolve_field(monitor, row, check.field) == check.threshold
    resolve_field(monitor, row, check.link_field)
