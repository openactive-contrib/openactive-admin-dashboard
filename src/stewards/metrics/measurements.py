"""One API response in, the rows to store out. Pure: no HTTP, no clock, no Streamlit.

Every function takes the period it is capturing and a response already read, and returns
measurements. Nothing here decides what a measure *is* — a row is a field the API reported,
so a definition that changes next year can be recomputed over every month already stored.

Two figures are kept twice on purpose:

- Completeness, `_weighted` and `_unweighted`. The quality endpoint averages its own summary
  unweighted, one feed one vote, while the measurement asks for a figure adjusted by volume
  of feed. Only the first exists in the payload, so the second is computed here from the
  per-feed counts. Which of the two the measure means is unsettled, and keeping one would
  settle it by accident.
- The labelled and the linked activity share, for the same reason.

The three monitor counts behind the critical-error measure are stored one per monitor rather
than summed, so the measure can be redefined without losing the months already captured.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from stewards.api.models import (
    ActivityCoverageSummaryResponse,
    AnalyticsSummary,
    EcosystemGrowthResponse,
    FeedQualityResponse,
    MetricMeasurement,
    SummaryResponse,
)

#: `source` on a stored row: which read the figure came from.
FEED_QUALITY = "feed_quality"
ACTIVITY_COVERAGE = "activity_coverage"
SUMMARY = "summary"
ANALYTICS_SUMMARY = "analytics_summary"
ECOSYSTEM_GROWTH = "ecosystem_growth"

#: Decimal places on a computed rate. The payload's own figures are stored as sent.
PRECISION = 4

#: The fleet figures worth a row of their own. `total_future_opportunity_items` is the
#: opportunity count the framework reports, and the weight behind every weighted average.
QUALITY_FIELDS = (
    "total_future_opportunity_items",
    "total_feeds",
    "feeds_scored",
    "average_score",
)

ACTIVITY_FIELDS = (
    "activity_opportunities",
    "labelled_opportunities",
    "labelled_percentage",
    "linked_opportunities",
    "linked_percentage",
    "activities_offered",
    "vocabulary_size",
)

SUMMARY_FIELDS = ("feeds", "datasets", "publishers_monitored", "publishers_with_issues")

ANALYTICS_FIELDS = (
    "number_of_facilities",
    "number_of_opportunities",
    "number_of_publishers",
    "number_of_activities",
    "number_of_facility_types",
    "number_of_activity_providers",
    "percentage_of_local_authorities",
)

#: The monitors the critical-error measure names. The other monitors are left out on purpose.
CRITICAL_ERROR_MONITORS = ("single_feed_stall", "dataset_stall", "feed_ingestion_error")


def measure(
    period: date,
    source: str,
    field: str,
    value: float | None,
    *,
    denominator: float | None = None,
    snapshot_date: date | None = None,
) -> MetricMeasurement:
    return MetricMeasurement(
        period=period,
        source=source,
        field=field,
        value=value,
        denominator=denominator,
        snapshot_date=snapshot_date,
    )


def _declared(
    period: date, source: str, data: object, fields: Sequence[str], snapshot: date | None
) -> list[MetricMeasurement]:
    """One row per named field, read straight off the model. A null stays null."""
    return [
        measure(period, source, field, getattr(data, field), snapshot_date=snapshot)
        for field in fields
    ]


def feed_quality_measurements(
    period: date, response: FeedQualityResponse
) -> tuple[MetricMeasurement, ...]:
    """The fleet figures, and completeness both weighted and unweighted.

    The weighted average is this file's one real piece of arithmetic. A feed counts in
    proportion to the future opportunities it publishes, so a feed with none carries no
    weight and drops out — it is not evidence about a fleet-wide rate, and counting it as a
    zero would drag the figure down for nothing. The denominator is how many feeds actually
    contributed, so a month computed over a handful of feeds can be seen for what it is.

    The field names come from the summary's own `completeness` block rather than a list
    here: which fields the batch assesses is the batch's to say.
    """
    snapshot = response.meta.snapshot_date
    summary = response.summary
    rows = _declared(period, FEED_QUALITY, summary, QUALITY_FIELDS, snapshot)

    for field in sorted(summary.completeness):
        numerator = 0.0
        weight_total = 0.0
        contributing = 0
        for feed in response.data:
            value = feed.completeness.get(field)
            weight = feed.num_future_opportunity_items
            if value is None or not weight or weight <= 0:
                continue
            numerator += value * weight
            weight_total += weight
            contributing += 1

        rows.append(
            measure(
                period,
                FEED_QUALITY,
                f"completeness_{field}_weighted",
                round(numerator / weight_total, PRECISION) if weight_total else None,
                denominator=contributing,
                snapshot_date=snapshot,
            )
        )

        stat = summary.completeness[field]
        rows.append(
            measure(
                period,
                FEED_QUALITY,
                f"completeness_{field}_unweighted",
                stat.average,
                denominator=stat.feeds_reporting,
                snapshot_date=snapshot,
            )
        )

    #: The weight the weighted averages were taken over, so one can be checked later. A
    #: single field's own total can be smaller, where some feeds did not report it.
    weighted = [
        feed.num_future_opportunity_items
        for feed in response.data
        if feed.num_future_opportunity_items and feed.num_future_opportunity_items > 0
    ]
    rows.append(
        measure(
            period,
            FEED_QUALITY,
            "completeness_weight_total",
            float(sum(weighted)),
            denominator=len(weighted),
            snapshot_date=snapshot,
        )
    )
    return tuple(rows)


def activity_measurements(
    period: date, response: ActivityCoverageSummaryResponse
) -> tuple[MetricMeasurement, ...]:
    """The national activity figures, both the labelled and the linked share."""
    return tuple(
        _declared(
            period,
            ACTIVITY_COVERAGE,
            response.data,
            ACTIVITY_FIELDS,
            response.meta.snapshot_date,
        )
    )


def summary_measurements(
    period: date, response: SummaryResponse
) -> tuple[MetricMeasurement, ...]:
    """The estate counts, and one row per monitor behind the critical-error measure.

    A monitor missing from the snapshot stores null rather than zero: the batch not having
    reported it is not the same as it having nothing to report.
    """
    snapshot = response.meta.snapshot_date
    rows = _declared(period, SUMMARY, response.data, SUMMARY_FIELDS, snapshot)

    for monitor_id in CRITICAL_ERROR_MONITORS:
        count = response.data.count_for(monitor_id)
        rows.append(
            measure(
                period,
                SUMMARY,
                f"past_threshold_{monitor_id}",
                count.past_threshold_count if count else None,
                denominator=count.count if count else None,
                snapshot_date=snapshot,
            )
        )
    return tuple(rows)


def analytics_measurements(
    period: date, summary: AnalyticsSummary
) -> tuple[MetricMeasurement, ...]:
    """The public surface's figures, dated by its own `date` rather than by a snapshot."""
    return tuple(
        _declared(period, ANALYTICS_SUMMARY, summary, ANALYTICS_FIELDS, summary.snapshot_date)
    )


def ecosystem_measurements(
    period: date, response: EcosystemGrowthResponse
) -> tuple[MetricMeasurement, ...]:
    """The month's crawl figure, or nothing at all.

    This is the one figure that is an aggregate over the month rather than a reading taken
    on a day, so it is only stored once the month has ended: an in-progress month covers
    the days so far and changes daily, and a captured measurement cannot be corrected. No
    rows come back for a month still running, or for one the series does not cover.
    """
    for point in response.data:
        if point.period_start != period:
            continue
        if point.in_progress:
            return ()
        return (
            measure(
                period,
                ECOSYSTEM_GROWTH,
                "datasets_crawled",
                point.datasets_crawled,
                denominator=point.days_with_data,
                snapshot_date=response.meta.snapshot_date,
            ),
        )
    return ()
