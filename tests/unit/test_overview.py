"""Overview tiles and sidebar labels."""

from __future__ import annotations

import pytest

from stewards.api.models import MonitorCount, Summary, SummaryResponse
from stewards.monitors.overview import (
    MINUS,
    STATE_LABELS,
    NavBadge,
    build_tiles,
    format_count,
    format_delta,
    nav_badges,
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
        # A count the API did not report is not an all-clear, and an unknown past-threshold
        # figure does not turn a non-zero count red on its own.
        (None, None, Tone.GREY),
        (None, 4, Tone.GREY),
        (5, None, Tone.AMBER),
        (0, None, Tone.GREEN),
    ],
)
def test_tile_state(count: int | None, past: int | None, expected: Tone) -> None:
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
        (None, None, "count not reported"),
        (5, None, "7-day threshold count not reported"),
    ],
)
def test_tile_note(count: int | None, past: int | None, fragment: str) -> None:
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


def test_a_count_the_api_did_not_report_gets_no_badge() -> None:
    """A null count carries no number, so there is nothing to put in the pill."""
    summary = Summary(
        publishers_monitored=170,
        past_threshold=None,
        monitors=(MonitorCount(monitor_id="single_feed_stall", count=None),),
    )
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


def test_a_null_count_reads_as_unknown_rather_than_zero() -> None:
    """The live admin API sends null for a figure it has not computed for this snapshot."""
    summary = Summary(
        monitors=(
            MonitorCount(monitor_id="single_feed_stall", count=None, sparkline=(1, None, 3)),
        ),
    )
    tile = next(t for t in build_tiles(summary) if t.monitor.id == "single_feed_stall")
    assert tile.count is None
    assert tile.value == "—"
    assert tile.state is Tone.GREY
    assert "not reported" in tile.note
    # Nulls are dropped from the line, not drawn as zeros.
    assert tile.sparkline == (1, 3)


def test_an_all_clear_snapshot_reads_green(payload) -> None:
    summary = SummaryResponse.model_validate(payload("summary_zero")).data
    assert all(t.state is Tone.GREEN for t in build_tiles(summary))
    assert all("no open incidents" in t.note for t in build_tiles(summary))


def test_sidebar_counts_maps_every_reported_monitor(summary: SummaryResponse) -> None:
    assert sidebar_counts(summary.data) == {"single_feed_stall": 23, "http_failure": 9}


def test_sidebar_counts_skips_a_monitor_whose_count_is_null() -> None:
    summary = Summary(
        monitors=(
            MonitorCount(monitor_id="single_feed_stall", count=None),
            MonitorCount(monitor_id="http_failure", count=9),
        )
    )
    assert sidebar_counts(summary) == {"http_failure": 9}


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


# --- counts the API does not report -------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (None, "—"),  # not reported: never rendered as a zero
        (0, "0"),
        (7, "7"),
        (1500, "1,500"),
    ],
)
def test_format_count(count: int | None, expected: str) -> None:
    assert format_count(count) == expected


def test_counts_default_to_absent_on_the_contract() -> None:
    """An absent count means the same as a null one: unknown, not zero."""
    summary = Summary()
    assert summary.publishers_monitored is None
    assert summary.open_incidents is None
    assert summary.past_threshold is None
    assert summary.feeds is None
    assert summary.datasets is None


def test_the_live_admin_summary_parses_with_its_nulls(payload) -> None:
    summary = SummaryResponse.model_validate(payload("admin_summary_partial")).data
    assert summary.open_incidents is None
    assert format_count(summary.open_incidents) == "—"
    assert format_count(summary.publishers_monitored) == "179"
    assert format_delta(summary.open_incidents_delta) is None
    assert format_delta(summary.past_threshold_delta) == "+2"
