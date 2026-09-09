"""Overview tiles and sidebar labels."""

from __future__ import annotations

from datetime import date

import pytest

from stewards.api.models import MonitorCount, Summary, SummaryResponse, TrendPoint
from stewards.monitors.health import Direction, HealthPolicy, HealthState
from stewards.monitors.overview import (
    MINUS,
    NavBadge,
    build_tiles,
    format_count,
    format_delta,
    monitor_health,
    nav_badges,
    series_from_counts,
    series_from_trend,
    sidebar_counts,
    tile_label,
    tile_note,
    tile_state,
)
from stewards.monitors.registry import MONITOR_REGISTRY, Severity, get_monitor
from stewards.monitors.thresholds import Tone


def counts(
    count: int | None,
    past: int | None = 0,
    sparkline: tuple[float | None, ...] = (),
    monitor_id: str = "single_feed_stall",
) -> MonitorCount:
    return MonitorCount(
        monitor_id=monitor_id, count=count, past_threshold_count=past, sparkline=sparkline
    )


def trend(
    open_counts: tuple[int, ...], past_counts: tuple[int, ...] = ()
) -> tuple[TrendPoint, ...]:
    """A daily series ending on 2026-08-21, the snapshot the fixtures describe."""
    past_counts = past_counts or (0,) * len(open_counts)
    start = date(2026, 8, 21).toordinal() - len(open_counts) + 1
    days = zip(open_counts, past_counts, strict=True)
    return tuple(
        TrendPoint(
            date=date.fromordinal(start + index),
            open_count=open_count,
            past_threshold_count=past_count,
        )
        for index, (open_count, past_count) in enumerate(days)
    )


@pytest.mark.parametrize(
    ("count", "past", "sparkline", "expected"),
    [
        (0, 0, (), Tone.GREEN),
        (1, 0, (1, 1, 1, 1, 1, 1, 1), Tone.AMBER),
        (1, 1, (1, 1, 1, 1, 1, 1, 1), Tone.RED),
        (23, 7, (12, 14, 15, 18, 20, 22, 23), Tone.RED),
        (23, 0, (23, 23, 23, 23, 23, 23, 23), Tone.AMBER),
        (-1, 0, (), Tone.GREEN),
        # A count the API did not report is not an all-clear, and an unknown past-threshold
        # figure does not turn a non-zero count red on its own.
        (None, None, (), Tone.GREY),
        (None, 4, (), Tone.GREY),
        (5, None, (5, 5, 5, 5, 5, 5, 5), Tone.AMBER),
        (0, None, (), Tone.GREEN),
    ],
)
def test_tile_state(
    count: int | None, past: int | None, sparkline: tuple[float, ...], expected: Tone
) -> None:
    monitor = get_monitor("single_feed_stall")
    health = monitor_health(monitor, counts(count, past, sparkline))
    assert tile_state(monitor, health) is expected


def test_a_rising_monitor_is_critical_before_anything_passes_the_threshold() -> None:
    """The whole point of judging on the series: deterioration shows before the backlog."""
    monitor = get_monitor("single_feed_stall")
    health = monitor_health(monitor, counts(19, 0), trend((10, 11, 12, 13, 14, 15, 17, 19)))
    assert health.state is HealthState.CRITICAL
    assert tile_state(monitor, health) is Tone.RED


def test_the_trend_series_is_preferred_over_the_summary_sparkline() -> None:
    """The sparkline is a week; the trend endpoint is the whole window the API keeps."""
    monitor = get_monitor("single_feed_stall")
    flat_week = (9, 9, 9, 9, 9, 9, 9)
    health = monitor_health(monitor, counts(9, 0, flat_week), trend((2, 3, 4, 5, 6, 7, 8, 9)))
    assert health.points == 8
    assert health.state is HealthState.CRITICAL


def test_a_series_the_api_orders_backwards_still_reads_left_to_right() -> None:
    points = tuple(reversed(trend((2, 3, 4, 5, 6, 7, 8, 9), (0, 0, 0, 0, 1, 1, 2, 3))))
    assert series_from_trend(points) == (
        (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0),
        (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0),
    )


def test_series_from_trend_of_no_points_is_empty() -> None:
    assert series_from_trend(()) == ((), ())


def test_the_count_is_appended_to_the_sparkline_only_when_it_is_newer() -> None:
    """The live API ends the sparkline on the count; a deployment need not."""
    assert series_from_counts(counts(23, 7, (20, 22, 23)))[0] == (20.0, 22.0, 23.0)
    assert series_from_counts(counts(23, 7, (20, 21, 22)))[0] == (20.0, 21.0, 22.0, 23.0)
    assert series_from_counts(counts(23, 7))[0] == (23.0,)


def test_series_from_counts_carries_the_threshold_figure_as_a_single_point() -> None:
    assert series_from_counts(counts(23, 7))[1] == (7.0,)
    assert series_from_counts(counts(23, None))[1] == ()


def informational_monitor() -> object:
    monitor = get_monitor("single_feed_stall")
    return type(monitor)(
        id="drift",
        name="Data schema drift",
        group=monitor.group,
        severity=Severity.INFORMATIONAL,
        blurb=monitor.blurb,
        unit="feeds drifted",
        columns=monitor.columns,
    )


def test_informational_monitor_stays_grey_until_past_threshold() -> None:
    monitor = informational_monitor()
    steady = monitor_health(monitor, counts(4, 0, (4, 4, 4, 4, 4, 4, 4)))
    backlog = monitor_health(monitor, counts(4, 1, (4, 4, 4, 4, 4, 4, 4)))
    assert tile_state(monitor, steady) is Tone.GREY
    assert tile_label(monitor, steady) == "Info"
    assert tile_state(monitor, backlog) is Tone.RED
    assert tile_label(monitor, backlog) == "Critical"


def test_a_monitor_declares_which_way_is_bad() -> None:
    """A volume monitor is judged on the same arithmetic, read the other way up."""
    monitor = get_monitor("single_feed_stall")
    volume = type(monitor)(
        id="opportunity_volume",
        name="Opportunity volume",
        group=monitor.group,
        severity=Severity.HIGH,
        blurb=monitor.blurb,
        unit="opportunities",
        columns=monitor.columns,
        health=HealthPolicy(
            direction=Direction.DOWN_IS_BAD, clear_level=None, secondary_escalates=False
        ),
    )
    falling = trend(tuple(round(400_000 * 0.95**i) for i in range(30)))
    assert monitor_health(volume, counts(280_000, 0), falling).state is HealthState.CRITICAL
    assert tile_state(volume, monitor_health(volume, counts(280_000, 0), falling)) is Tone.RED


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
    assert fragment in tile_note(get_monitor("feed_ingestion_error"), count, past)


def test_nav_badges_carry_the_count_and_the_tile_tone(summary: SummaryResponse) -> None:
    badges = nav_badges(summary.data)
    assert badges["single_feed_stall"] == NavBadge("23", Tone.RED)
    assert badges["feed_ingestion_error"] == NavBadge("9", Tone.RED)
    assert badges["contact_queue"] == NavBadge("10", Tone.RED)


def test_a_monitor_with_nothing_open_gets_no_badge() -> None:
    summary = Summary(
        publishers_monitored=170,
        monitors=(
            MonitorCount(monitor_id="single_feed_stall", count=0),
            MonitorCount(monitor_id="feed_ingestion_error", count=4, past_threshold_count=0),
        ),
    )
    badges = nav_badges(summary)
    assert "single_feed_stall" not in badges
    assert badges["feed_ingestion_error"] == NavBadge("4", Tone.AMBER)


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
        if tile.count is not None and tile.count > 0:
            assert badges[tile.monitor.id].tone is tile.state


def test_tiles_cover_the_whole_registry_in_order(summary: SummaryResponse) -> None:
    tiles = build_tiles(summary.data)
    assert [t.monitor.id for t in tiles] == [m.id for m in MONITOR_REGISTRY]


def test_a_tile_with_no_history_says_nothing_about_a_trend(summary: SummaryResponse) -> None:
    """One point is not a trend, and the count line above already says what there is."""
    tiles = {t.monitor.id: t for t in build_tiles(Summary(publishers_monitored=170))}
    assert tiles["single_feed_stall"].trend_note == ""
    stalls = next(t for t in build_tiles(summary.data) if t.monitor.id == "single_feed_stall")
    assert "across 7 snapshots" in stalls.trend_note


def test_tile_values_come_from_the_summary(summary: SummaryResponse) -> None:
    stalls = next(t for t in build_tiles(summary.data) if t.monitor.id == "single_feed_stall")
    assert stalls.count == 23
    assert stalls.past_threshold_count == 7
    assert stalls.value == "23"
    assert stalls.state is Tone.RED
    assert stalls.state_label == "Critical"
    assert stalls.sparkline == (12, 14, 15, 18, 20, 22, 23)


def test_a_monitor_the_api_does_not_report_is_shown_at_zero() -> None:
    summary = Summary(
        publishers_monitored=170,
        monitors=(MonitorCount(monitor_id="single_feed_stall", count=3),),
    )
    tiles = {t.monitor.id: t for t in build_tiles(summary)}
    assert tiles["feed_ingestion_error"].count == 0
    assert tiles["feed_ingestion_error"].state is Tone.GREEN
    assert tiles["feed_ingestion_error"].sparkline == ()


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
    assert sidebar_counts(summary.data) == {"single_feed_stall": 23, "feed_ingestion_error": 9}


def test_sidebar_counts_skips_a_monitor_whose_count_is_null() -> None:
    summary = Summary(
        monitors=(
            MonitorCount(monitor_id="single_feed_stall", count=None),
            MonitorCount(monitor_id="feed_ingestion_error", count=9),
        )
    )
    assert sidebar_counts(summary) == {"feed_ingestion_error": 9}


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
