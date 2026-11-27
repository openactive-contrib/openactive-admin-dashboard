"""Turning a response into rows, and the volume-weighted arithmetic behind KPI4.

The weighting is the part most likely to be quietly wrong — it is the only figure the
capture computes rather than copies — so it is asserted at each of its boundaries.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fixture_loader import load_sample
from stewards.api.models import (
    ActivityCoverageSummaryResponse,
    AnalyticsSummary,
    EcosystemGrowthResponse,
    FeedQualityResponse,
    MetricMeasurement,
    SummaryResponse,
)
from stewards.metrics import measurements

PERIOD = date(2026, 11, 1)
META = {"snapshot_date": "2026-11-30", "generated_at": "2026-12-01T07:00:00Z"}


def quality(feeds: list[dict[str, Any]], completeness: dict[str, Any]) -> FeedQualityResponse:
    return FeedQualityResponse.model_validate(
        {"data": feeds, "summary": {"completeness": completeness}, "meta": META}
    )


def feed(weight: int | None, **fields: float | None) -> dict[str, Any]:
    return {"num_future_opportunity_items": weight, "completeness": fields}


def value_of(rows: tuple[MetricMeasurement, ...], field: str) -> MetricMeasurement:
    return next(row for row in rows if row.field == field)


# --- the volume-weighted average ------------------------------------------------------


def test_a_feed_counts_in_proportion_to_what_it_publishes() -> None:
    """Nine tenths of the opportunities score 100, one tenth scores 0, so the weighted
    figure is 90 where one-feed-one-vote would call it 50."""
    rows = measurements.feed_quality_measurements(
        PERIOD,
        quality(
            [feed(900, location=100.0), feed(100, location=0.0)],
            {"location": {"average": 50.0, "feeds_reporting": 2}},
        ),
    )
    assert value_of(rows, "completeness_location_weighted").value == 90.0
    assert value_of(rows, "completeness_location_unweighted").value == 50.0


def test_the_weighted_denominator_counts_the_feeds_that_contributed() -> None:
    rows = measurements.feed_quality_measurements(
        PERIOD,
        quality(
            [feed(900, location=100.0), feed(100, location=0.0), feed(0, location=100.0)],
            {"location": {"average": 66.6, "feeds_reporting": 3}},
        ),
    )
    assert value_of(rows, "completeness_location_weighted").denominator == 2


def test_a_feed_with_nothing_in_the_future_carries_no_weight() -> None:
    """It is not evidence about a fleet-wide rate, and counting it as a zero would drag
    the figure down for nothing."""
    with_idle = measurements.feed_quality_measurements(
        PERIOD,
        quality(
            [feed(900, location=100.0), feed(0, location=0.0)],
            {"location": {"average": 50.0}},
        ),
    )
    assert value_of(with_idle, "completeness_location_weighted").value == 100.0


def test_a_feed_that_did_not_report_the_field_is_left_out() -> None:
    rows = measurements.feed_quality_measurements(
        PERIOD,
        quality(
            [feed(900, location=80.0), feed(100, location=None)],
            {"location": {"average": 80.0}},
        ),
    )
    assert value_of(rows, "completeness_location_weighted").value == 80.0
    assert value_of(rows, "completeness_location_weighted").denominator == 1


def test_no_weight_at_all_stores_null_rather_than_zero() -> None:
    rows = measurements.feed_quality_measurements(
        PERIOD, quality([feed(0, location=100.0)], {"location": {"average": 100.0}})
    )
    weighted = value_of(rows, "completeness_location_weighted")
    assert weighted.value is None
    assert weighted.denominator == 0


def test_a_missing_weight_is_treated_as_no_weight() -> None:
    rows = measurements.feed_quality_measurements(
        PERIOD, quality([feed(None, location=100.0)], {"location": {"average": 100.0}})
    )
    assert value_of(rows, "completeness_location_weighted").value is None


def test_the_weighted_figure_is_rounded_to_four_places() -> None:
    rows = measurements.feed_quality_measurements(
        PERIOD,
        quality([feed(3, location=100.0), feed(7, location=0.0)], {"location": {}}),
    )
    assert value_of(rows, "completeness_location_weighted").value == 30.0


# --- the rest of the quality snapshot --------------------------------------------------


def test_the_unweighted_figure_is_copied_with_its_own_denominator() -> None:
    rows = measurements.feed_quality_measurements(
        PERIOD,
        quality(
            [feed(10, location=90.0)],
            {"location": {"average": 91.5, "feeds_reporting": 8}},
        ),
    )
    unweighted = value_of(rows, "completeness_location_unweighted")
    assert unweighted.value == 91.5
    assert unweighted.denominator == 8


def test_the_fields_come_from_the_snapshot_not_from_a_list_here() -> None:
    """Which fields the batch assesses is the batch's to say."""
    rows = measurements.feed_quality_measurements(
        PERIOD, quality([feed(5, something_new=70.0)], {"something_new": {"average": 70.0}})
    )
    assert value_of(rows, "completeness_something_new_weighted").value == 70.0


def test_the_total_weight_sums_the_feeds_that_carried_one() -> None:
    rows = measurements.feed_quality_measurements(
        PERIOD, quality([feed(900), feed(100), feed(0)], {})
    )
    total = value_of(rows, "completeness_weight_total")
    assert total.value == 1000
    assert total.denominator == 2


def test_an_empty_quality_snapshot_still_stores_its_fleet_fields() -> None:
    rows = measurements.feed_quality_measurements(PERIOD, quality([], {}))
    assert value_of(rows, "total_feeds").value is None
    assert value_of(rows, "completeness_weight_total").value == 0


def test_the_real_quality_snapshot_stores_both_figures_for_every_field() -> None:
    response = FeedQualityResponse.model_validate(load_sample("feed_quality_quality"))
    rows = measurements.feed_quality_measurements(PERIOD, response)

    assert value_of(rows, "total_future_opportunity_items").value == 182125
    assert value_of(rows, "feeds_scored").value == 11
    weighted = [row for row in rows if row.field.endswith("_weighted")]
    unweighted = [row for row in rows if row.field.endswith("_unweighted")]
    assert len(weighted) == len(unweighted) == 9
    assert all(row.snapshot_date == date(2026, 8, 21) for row in rows)


# --- the fleet summary ------------------------------------------------------------------


def test_the_summary_stores_its_estate_counts() -> None:
    response = SummaryResponse.model_validate(load_sample("summary"))
    rows = measurements.summary_measurements(PERIOD, response)
    assert value_of(rows, "publishers_monitored").value == 170
    assert all(row.period == PERIOD for row in rows)


def test_each_critical_error_monitor_is_stored_on_its_own() -> None:
    """Summed here, the measure could never be redefined without losing the months
    already captured."""
    response = SummaryResponse.model_validate(load_sample("summary"))
    rows = measurements.summary_measurements(PERIOD, response)

    stall = value_of(rows, "past_threshold_single_feed_stall")
    assert stall.value == 7
    assert stall.denominator is not None


def test_a_monitor_the_snapshot_does_not_report_stores_null() -> None:
    response = SummaryResponse.model_validate(
        {"data": {"feeds": 446, "monitors": []}, "meta": META}
    )
    rows = measurements.summary_measurements(PERIOD, response)
    for monitor_id in measurements.CRITICAL_ERROR_MONITORS:
        row = value_of(rows, f"past_threshold_{monitor_id}")
        assert row.value is None
        assert row.denominator is None


def test_a_count_the_batch_did_not_compute_stays_null() -> None:
    response = SummaryResponse.model_validate(
        {"data": {"feeds": 446, "datasets": None}, "meta": META}
    )
    rows = measurements.summary_measurements(PERIOD, response)
    assert value_of(rows, "datasets").value is None


def test_a_genuine_zero_is_stored_as_a_zero() -> None:
    """Null and zero are different answers, and the series has to keep them apart."""
    response = SummaryResponse.model_validate(load_sample("admin_summary_partial"))
    rows = measurements.summary_measurements(PERIOD, response)
    assert value_of(rows, "publishers_with_issues").value == 0


# --- activity coverage and the analytics summary ------------------------------------------


def test_both_activity_shares_are_stored() -> None:
    response = ActivityCoverageSummaryResponse.model_validate(
        load_sample("activity_coverage_summary")
    )
    rows = measurements.activity_measurements(PERIOD, response)
    assert value_of(rows, "labelled_percentage").value == 65.5
    assert value_of(rows, "linked_percentage").value == 53.6


def test_the_analytics_figures_are_dated_by_their_own_run() -> None:
    summary = AnalyticsSummary.model_validate(load_sample("analytics_summary"))
    rows = measurements.analytics_measurements(PERIOD, summary)
    assert value_of(rows, "number_of_facilities").value == 7708
    assert all(row.snapshot_date == date(2026, 11, 25) for row in rows)


def test_analytics_figures_that_never_arrived_are_stored_as_null() -> None:
    rows = measurements.analytics_measurements(PERIOD, AnalyticsSummary())
    assert len(rows) == len(measurements.ANALYTICS_FIELDS)
    assert all(row.value is None and row.snapshot_date is None for row in rows)


# --- the month's crawl figure ---------------------------------------------------------


def test_a_finished_month_stores_its_crawl_figure() -> None:
    response = EcosystemGrowthResponse.model_validate(load_sample("ecosystem_growth"))
    (row,) = measurements.ecosystem_measurements(date(2026, 10, 1), response)
    assert row.value == 174
    assert row.denominator == 31
    assert row.snapshot_date == date(2026, 11, 13)


def test_a_month_still_running_stores_nothing() -> None:
    """Its figure covers the days so far and changes daily, and a captured measurement
    cannot be corrected."""
    response = EcosystemGrowthResponse.model_validate(load_sample("ecosystem_growth"))
    assert measurements.ecosystem_measurements(date(2026, 11, 1), response) == ()


def test_a_month_the_series_does_not_reach_stores_nothing() -> None:
    response = EcosystemGrowthResponse.model_validate(load_sample("ecosystem_growth"))
    assert measurements.ecosystem_measurements(date(2020, 1, 1), response) == ()


def test_an_empty_growth_series_stores_nothing() -> None:
    response = EcosystemGrowthResponse.model_validate({"data": [], "meta": META})
    assert measurements.ecosystem_measurements(PERIOD, response) == ()
