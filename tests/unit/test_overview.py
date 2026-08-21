"""Overview tiles and sidebar labels."""

from __future__ import annotations

import pytest

from stewards.api.models import MonitorCount, Summary, SummaryResponse
from stewards.monitors.overview import (
    STATE_LABELS,
    build_tiles,
    nav_label,
    sidebar_counts,
    tile_note,
    tile_state,
)
from stewards.monitors.registry import MONITOR_REGISTRY, Severity, get_monitor
from stewards.monitors.thresholds import Tone


@pytest.mark.parametrize(
    ("count", "past", "expected"),
    [
        (0, 0, Tone.GREEN),
        (1, 0, Tone.AMBER),
        (1, 1, Tone.RED),
        (23, 7, Tone.RED),
        (23, 0, Tone.AMBER),
        (-1, 0, Tone.GREEN),
    ],
)
def test_tile_state(count: int, past: int, expected: Tone) -> None:
    assert tile_state(get_monitor("single_feed_stall"), count, past) is expected


def test_informational_monitor_stays_grey_until_past_threshold() -> None:
    monitor = get_monitor("single_feed_stall")
    informational = type(monitor)(
        id="drift",
        name="Data schema drift",
        group=monitor.group,
        severity=Severity.INFORMATIONAL,
        blurb=monitor.blurb,
        unit="feeds drifted",
        columns=monitor.columns,
    )
    assert tile_state(informational, 4, 0) is Tone.GREY
    assert tile_state(informational, 4, 1) is Tone.RED


@pytest.mark.parametrize(
    ("count", "past", "fragment"),
    [
        (0, 0, "no open incidents"),
        (23, 7, "7 past the 7-day threshold"),
        (5, 0, "none past the 7-day threshold yet"),
    ],
)
def test_tile_note(count: int, past: int, fragment: str) -> None:
    assert fragment in tile_note(get_monitor("http_failure"), count, past)


@pytest.mark.parametrize(
    ("count", "expected"),
    [(None, "Contact queue"), (0, "Contact queue (0)"), (14, "Contact queue (14)")],
)
def test_nav_label(count: int | None, expected: str) -> None:
    assert nav_label("Contact queue", count) == expected


def test_tiles_cover_the_whole_registry_in_order(summary: SummaryResponse) -> None:
    tiles = build_tiles(summary.data)
    assert [t.monitor.id for t in tiles] == [m.id for m in MONITOR_REGISTRY]


def test_tile_values_come_from_the_summary(summary: SummaryResponse) -> None:
    stalls = next(t for t in build_tiles(summary.data) if t.monitor.id == "single_feed_stall")
    assert stalls.count == 23
    assert stalls.past_threshold_count == 7
    assert stalls.value == "23"
    assert stalls.state is Tone.RED
    assert stalls.state_label == STATE_LABELS[Tone.RED]
    assert stalls.sparkline == (12, 14, 15, 18, 20, 22, 23)


def test_a_monitor_the_api_does_not_report_is_shown_at_zero() -> None:
    summary = Summary(
        publishers_monitored=170,
        monitors=(MonitorCount(monitor_id="single_feed_stall", count=3),),
    )
    tiles = {t.monitor.id: t for t in build_tiles(summary)}
    assert tiles["http_failure"].count == 0
    assert tiles["http_failure"].state is Tone.GREEN
    assert tiles["http_failure"].sparkline == ()


def test_an_all_clear_snapshot_reads_green(payload) -> None:
    summary = SummaryResponse.model_validate(payload("summary_zero")).data
    assert all(t.state is Tone.GREEN for t in build_tiles(summary))
    assert all("no open incidents" in t.note for t in build_tiles(summary))


def test_sidebar_counts_maps_every_reported_monitor(summary: SummaryResponse) -> None:
    assert sidebar_counts(summary.data) == {"single_feed_stall": 23, "http_failure": 9}


def test_sidebar_counts_of_an_empty_summary_is_empty() -> None:
    assert sidebar_counts(Summary()) == {}
