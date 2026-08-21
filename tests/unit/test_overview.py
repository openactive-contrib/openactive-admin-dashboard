"""Overview tiles and sidebar labels."""

from __future__ import annotations

import pytest

from stewards.api.models import MonitorCount, Summary, SummaryResponse
from stewards.monitors.overview import (
    MINUS,
    OVERVIEW_COLUMNS,
    STATE_LABELS,
    NavBadge,
    build_tiles,
    format_delta,
    nav_badges,
    overview_frame,
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


def test_nav_badges_carry_the_count_and_the_tile_tone(summary: SummaryResponse) -> None:
    badges = nav_badges(summary.data)
    assert badges["single_feed_stall"] == NavBadge("23", Tone.RED)
    assert badges["http_failure"] == NavBadge("9", Tone.RED)
    assert badges["contact_queue"] == NavBadge("10", Tone.RED)


def test_a_monitor_with_nothing_open_gets_no_badge() -> None:
    summary = Summary(
        publishers_monitored=170,
        monitors=(
            MonitorCount(monitor_id="single_feed_stall", count=0),
            MonitorCount(monitor_id="http_failure", count=4, past_threshold_count=0),
        ),
    )
    badges = nav_badges(summary)
    assert "single_feed_stall" not in badges
    assert badges["http_failure"] == NavBadge("4", Tone.AMBER)


def test_no_contact_queue_badge_when_nothing_is_past_threshold() -> None:
    assert "contact_queue" not in nav_badges(Summary(publishers_monitored=170))


def test_an_all_clear_snapshot_has_no_badges_at_all(payload) -> None:
    summary = SummaryResponse.model_validate(payload("summary_zero")).data
    assert nav_badges(summary) == {}


def test_a_monitor_the_api_does_not_report_gets_no_badge() -> None:
    assert nav_badges(Summary(publishers_monitored=170)) == {}


def test_badge_tone_agrees_with_the_tile_tone(summary: SummaryResponse) -> None:
    """The sidebar and the overview must never disagree about a monitor's state."""
    badges = nav_badges(summary.data)
    for tile in build_tiles(summary.data):
        if tile.count > 0:
            assert badges[tile.monitor.id].tone is tile.state


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


# --- deltas -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (None, None),  # the API has not sent it: render nothing, never a made-up zero
        (0, "0"),
        (3, "+3"),
        (-2, f"{MINUS}2"),
        (1500, "+1,500"),
        (-1500, f"{MINUS}1,500"),
        (1, "+1"),
        (-1, f"{MINUS}1"),
    ],
)
def test_format_delta(change: int | None, expected: str | None) -> None:
    assert format_delta(change) == expected


def test_a_negative_delta_uses_a_true_minus_not_a_hyphen() -> None:
    rendered = format_delta(-4)
    assert rendered is not None
    assert "-" not in rendered
    assert rendered.startswith(MINUS)


def test_deltas_default_to_absent_on_the_contract() -> None:
    summary = Summary()
    assert summary.publishers_with_issues_delta is None
    assert summary.open_incidents_delta is None
    assert summary.past_threshold_delta is None


def test_the_sample_summary_supplies_deltas(summary: SummaryResponse) -> None:
    assert format_delta(summary.data.publishers_with_issues_delta) == "+3"
    assert format_delta(summary.data.open_incidents_delta) == "+5"
    assert format_delta(summary.data.past_threshold_delta) == "+2"


# --- the export frame ---------------------------------------------------------------------


def test_overview_frame_has_a_row_per_tile(summary: SummaryResponse) -> None:
    frame = overview_frame(build_tiles(summary.data))
    assert list(frame.columns) == list(OVERVIEW_COLUMNS)
    assert len(frame) == len(MONITOR_REGISTRY)
    assert set(frame["Monitor"]) == {m.name for m in MONITOR_REGISTRY}


def test_overview_frame_carries_the_counts(summary: SummaryResponse) -> None:
    frame = overview_frame(build_tiles(summary.data)).set_index("Monitor")
    assert frame.loc["Single-feed stalls", "Open"] == 23
    assert frame.loc["Single-feed stalls", "Past threshold"] == 7
    assert frame.loc["Single-feed stalls", "State"] == "Critical"


def test_overview_frame_of_no_tiles_keeps_its_columns() -> None:
    frame = overview_frame([])
    assert frame.empty
    assert list(frame.columns) == list(OVERVIEW_COLUMNS)
