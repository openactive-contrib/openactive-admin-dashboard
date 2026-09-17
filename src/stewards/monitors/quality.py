"""The fleet quality snapshot -> rows, figures and charts. No Streamlit, no I/O.

`transforms.py` is this module's opposite number: it shapes a list of incidents that age,
which is what every other monitor reports. A quality assessment is a different animal — one
row per feed with this snapshot's measurements, a fleet-wide `summary` block beside them and
no history at all — so it gets its own shaping rather than being forced into an `Incident`
whose `first_detected`, `days_open` and `past_threshold` would all have to be invented.

What it does share is everything below the data: the registry declares the columns, the
column kinds format and shade the cells exactly as they do on an incident table, the tones
come from `thresholds`, and the card's verdict is a `Health`, so the chip, the tone and the
sidebar pill run through the code every other monitor uses.

Pure, as `trend.py` and `gauge.py` are: the chart builders take their colours from the
caller rather than importing the theme.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Any
from urllib.parse import urlparse

import altair as alt
import pandas as pd

from stewards.api.models import FeedQualityFeed, FeedQualitySummary, QualityBreakdown
from stewards.monitors.health import Health, HealthState, Movement
from stewards.monitors.overview import Fact, TileCard
from stewards.monitors.registry import Col, ColKind, Monitor
from stewards.monitors.thresholds import Tone, risk_tone, score_tone, status_label, status_tone
from stewards.monitors.transforms import EMPTY, format_cell

#: What a categorical field reads as when the assessment did not report one. A real string
#: rather than a blank, so it is a filter option a steward can actually select.
UNKNOWN = "Unknown"

#: Sorts a row the batch could not score after every scored one, without conflating it with
#: a genuine zero — which is a real score and belongs at the bottom of the scored rows.
NO_SCORE = -1.0

NOT_SCORED = "no quality score reported in this snapshot"


# --- rows ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QualityRow:
    """One feed as the table shows it: the assessment's figures plus the few it derives."""

    publisher_id: str
    publisher_name: str
    dataset_key: str
    dataset_name: str
    dataset_url: str

    #: The mean score of the feeds in this row's dataset, so the table can order whole
    #: datasets rather than interleaving their feeds. None where none of them was scored.
    dataset_score: float | None

    feed_id: str
    feed_name: str
    feed_url: str
    feed_type: str
    feed_version: str
    regularity: str
    status: str
    grade: str
    score: float | None
    completeness_percent: float | None
    future_items: int | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    completeness: Mapping[str, float | None]
    missing_required_fields: Mapping[str, tuple[str, ...]]
    last_assessed: str

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def issue_count(self) -> int:
        return self.error_count + self.warning_count


def feed_label(feed: FeedQualityFeed) -> str:
    """The feed's last URL segment — what a steward reads it by — falling back to its id."""
    path = urlparse(feed.feed_url).path.rstrip("/")
    segment = path.rsplit("/", 1)[-1] if path else ""
    return segment or feed.feed_id or UNKNOWN


def dataset_label(feed: FeedQualityFeed) -> str:
    """The dataset's name, or its URL where the batch reported no name for it."""
    return feed.dataset_name.strip() or feed.dataset_url.strip() or UNKNOWN


def dataset_key(feed: FeedQualityFeed) -> str:
    """What groups a publisher's feeds. The URL identifies a dataset; the name need not —
    several publishers report the same generic name, and some report none at all."""
    return feed.dataset_url.strip() or dataset_label(feed)


def regularity(feed: FeedQualityFeed) -> str:
    if feed.is_regular is None:
        return UNKNOWN
    return "Regular" if feed.is_regular else "Irregular"


def mean_completeness(values: Mapping[str, float | None]) -> float | None:
    """Mean coverage across the recommended fields this assessment actually reported.

    None rather than zero when it reported none: a field the assessment did not reach has no
    coverage to average, and a zero would read as "nothing is populated".
    """
    reported = [float(value) for value in values.values() if value is not None]
    return fmean(reported) if reported else None


def dataset_scores(feeds: Sequence[FeedQualityFeed]) -> dict[str, float | None]:
    """Mean score per dataset, over the feeds in it that were scored."""
    scores: dict[str, list[float]] = {}
    for feed in feeds:
        bucket = scores.setdefault(dataset_key(feed), [])
        if feed.score is not None:
            bucket.append(float(feed.score))
    return {key: (fmean(values) if values else None) for key, values in scores.items()}


def build_rows(feeds: Sequence[FeedQualityFeed]) -> tuple[QualityRow, ...]:
    """One row per assessed feed, each carrying its dataset's mean score."""
    by_dataset = dataset_scores(feeds)
    return tuple(
        QualityRow(
            publisher_id=feed.publisher_id,
            publisher_name=feed.publisher_name or UNKNOWN,
            dataset_key=dataset_key(feed),
            dataset_name=dataset_label(feed),
            dataset_url=feed.dataset_url,
            dataset_score=by_dataset.get(dataset_key(feed)),
            feed_id=feed.feed_id,
            feed_name=feed_label(feed),
            feed_url=feed.feed_url,
            feed_type=feed.feed_type.strip() or UNKNOWN,
            feed_version=feed.feed_version.strip() or UNKNOWN,
            regularity=regularity(feed),
            status=feed.status,
            grade=(feed.grade or "").strip().capitalize() or UNKNOWN,
            score=feed.score,
            completeness_percent=mean_completeness(feed.completeness),
            future_items=feed.num_future_opportunity_items,
            errors=feed.errors,
            warnings=feed.warnings,
            completeness=feed.completeness,
            missing_required_fields=feed.missing_required_fields,
            last_assessed=(
                feed.last_assessed.date().isoformat() if feed.last_assessed else EMPTY
            ),
        )
        for feed in feeds
    )


def resolve(row: QualityRow, field: str) -> Any:
    """Read a declared column or filter field off a row. Flat by design: every figure a
    quality row reports is an attribute of the row, so there is no path syntax to learn."""
    return getattr(row, field, None)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def sort_rows(monitor: Monitor, rows: Sequence[QualityRow]) -> list[QualityRow]:
    """Best dataset first, with a dataset's own feeds kept together beneath it, best first.

    Two keys rather than one: the monitor's `sort_field` orders the datasets, and the
    dataset's own key follows it, so two datasets that score alike cannot interleave their
    feeds. A dataset or feed the batch could not score sorts after every scored one rather
    than as a zero.
    """

    def key(row: QualityRow) -> tuple[float, str, str, float, str]:
        dataset = _numeric(resolve(row, monitor.sort_field))
        return (
            -(dataset if dataset is not None else NO_SCORE),
            row.dataset_name.lower(),
            row.dataset_key,
            -(row.score if row.score is not None else NO_SCORE),
            row.feed_name.lower(),
        )

    return sorted(rows, key=key)


# --- the table ----------------------------------------------------------------------------


def cell_tone(col: Col, value: Any) -> Tone | None:
    """RAG tone for a cell, or None where the column carries no RAG meaning.

    Unlike an incident table, every tone here is read off the cell's own value: a quality row
    has no age to shade and no contact threshold to shade it against.
    """
    match col.kind:
        case ColKind.STATUS:
            return status_tone(str(value or ""))
        case ColKind.PERCENT | ColKind.SCORE:
            return score_tone(None if value is None else float(value))
        case ColKind.RISK:
            return risk_tone(None if value is None else float(value))
        case _:
            return None


def to_dataframe(monitor: Monitor, rows: Sequence[QualityRow]) -> pd.DataFrame:
    """One line per row, columns in the order the registry declares.

    Zero rows yields an empty frame with the declared columns, so the table renders empty
    instead of raising.
    """
    labels = [col.label for col in monitor.columns]
    records = [
        {col.label: format_cell(col, resolve(row, col.field)) for col in monitor.columns}
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels)


def tone_frame(monitor: Monitor, rows: Sequence[QualityRow]) -> pd.DataFrame:
    """Tone name per cell, aligned with `to_dataframe`; empty string where unstyled."""
    labels = [col.label for col in monitor.columns]
    records = [
        {
            col.label: (tone.value if (tone := cell_tone(col, resolve(row, col.field))) else "")
            for col in monitor.columns
        }
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels).fillna("")


# --- filtering ----------------------------------------------------------------------------


def filter_options(rows: Sequence[QualityRow], field: str) -> list[str]:
    """Distinct non-empty values for a filter field, sorted, for a selectbox."""
    return sorted(
        {str(value) for row in rows if (value := resolve(row, field)) not in (None, "")}
    )


def search_rows(rows: Iterable[QualityRow], term: str) -> list[QualityRow]:
    """Case-insensitive substring match on publisher, dataset, feed name and feed id."""
    needle = term.strip().lower()
    if not needle:
        return list(rows)
    return [
        row
        for row in rows
        if needle
        in " ".join(
            part.lower()
            for part in (row.publisher_name, row.dataset_name, row.feed_name, row.feed_id)
        )
    ]


def apply_filters(
    rows: Sequence[QualityRow],
    *,
    search: str = "",
    selections: Mapping[str, str] | None = None,
    issues_only: bool = False,
) -> list[QualityRow]:
    """Search, per-field selections and the issues toggle, applied in that order.

    Local to the cached snapshot, as on every other page, so the controls respond without a
    refetch and stay unit-testable.
    """
    result = search_rows(rows, search)
    for field, wanted in (selections or {}).items():
        if not wanted:
            continue
        result = [row for row in result if str(resolve(row, field) or "") == wanted]
    if issues_only:
        result = [row for row in result if row.issue_count]
    return result


# --- the figures --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stat:
    """One KPI block. `tone=None` leaves the value in body ink, for context rather than a
    state — the same bargain `layout.tone_metric` makes."""

    label: str
    value: str
    tone: Tone | None = None
    sub: str = ""


def format_number(count: int | None) -> str:
    """A count for display. None is a figure the batch did not report, never a zero."""
    return EMPTY if count is None else f"{count:,}"


def format_score(score: float | None) -> str:
    return EMPTY if score is None else f"{score:.1f}"


def _count_tone(count: int | None, tone: Tone) -> Tone | None:
    """A figure that was not reported carries no state, and a zero is nothing to flag."""
    if count is None:
        return None
    return tone if count else Tone.GREY


def page_stats(summary: FeedQualitySummary) -> tuple[Stat, ...]:
    """The five figures above the quality table, straight off the snapshot's summary block."""
    return (
        Stat(
            "Average score",
            format_score(summary.average_score),
            None if summary.average_score is None else score_tone(summary.average_score),
            f"median {format_score(summary.median_score)}",
        ),
        Stat(
            "Feeds scored",
            format_number(summary.feeds_scored),
            None,
            f"of {format_number(summary.total_feeds)} feeds assessed",
        ),
        Stat(
            "Feeds with errors",
            format_number(summary.feeds_with_errors),
            _count_tone(summary.feeds_with_errors, Tone.RED),
            f"across {format_number(summary.datasets_with_errors)} datasets",
        ),
        Stat(
            "Feeds with warnings",
            format_number(summary.feeds_with_warnings),
            _count_tone(summary.feeds_with_warnings, Tone.AMBER),
            f"{format_number(summary.feeds_ok)} feeds reported OK",
        ),
        Stat(
            "Future opportunities",
            format_number(summary.total_future_opportunity_items),
            None,
            f"across {format_number(summary.feeds_with_future_data)} feeds",
        ),
    )


def assess_quality(summary: FeedQualitySummary) -> Health:
    """The card's verdict, taken from the fleet's average score.

    The bargain `gauge.assess_benchmark` makes, for the same reason: a snapshot with no
    history behind it cannot be judged on movement, so the state comes from the level alone
    and `movement` stays unknown rather than claiming a direction the data cannot support.
    The bands are `thresholds.score_tone`, which is what shades every other quality figure in
    the app, so the card and the table's Score column cannot tell different stories.
    """
    average = summary.average_score
    if average is None:
        return Health(HealthState.UNKNOWN, Movement.UNKNOWN, NOT_SCORED)
    states = {Tone.GREEN: HealthState.HEALTHY, Tone.AMBER: HealthState.WARNING}
    state = states.get(score_tone(average), HealthState.CRITICAL)
    reason = (
        f"{format_score(average)} average score across "
        f"{format_number(summary.feeds_scored)} scored feeds"
    )
    return Health(state, Movement.UNKNOWN, reason, current=average)


def tile_note(summary: FeedQualitySummary) -> str:
    if summary.average_score is None:
        return NOT_SCORED
    return (
        f"{format_number(summary.feeds_scored)} of {format_number(summary.total_feeds)} "
        "feeds scored in this snapshot"
    )


def tile_card(summary: FeedQualitySummary) -> TileCard:
    """The overview card: the average score, four supporting figures and the verdict.

    A single count would say nothing useful here — "73" alone carries neither how many feeds
    were assessed nor how many of them carry an error — so the card states the figures the
    monitor is actually about. See `tile_viz.Facts`.
    """
    return TileCard(
        value=format_score(summary.average_score),
        unit="average quality score",
        health=assess_quality(summary),
        facts=(
            Fact(
                "Feeds OK",
                format_number(summary.feeds_ok),
                _count_tone(summary.feeds_ok, Tone.GREEN),
            ),
            Fact(
                "Warnings",
                format_number(summary.feeds_with_warnings),
                _count_tone(summary.feeds_with_warnings, Tone.AMBER),
            ),
            Fact(
                "Errors",
                format_number(summary.feeds_with_errors),
                _count_tone(summary.feeds_with_errors, Tone.RED),
            ),
            Fact(
                "Datasets with errors",
                format_number(summary.datasets_with_errors),
                _count_tone(summary.datasets_with_errors, Tone.RED),
            ),
        ),
        note=tile_note(summary),
        badge=summary.feeds_with_errors,
    )


# --- the selected row ---------------------------------------------------------------------


def field_label(field: str) -> str:
    """`start_date` -> `Start date`. The batch owns which fields it reports, so their labels
    are de-slugged rather than declared."""
    return field.replace("_", " ").capitalize() or UNKNOWN


def completeness_frame(row: QualityRow) -> pd.DataFrame:
    """The selected feed's coverage per recommended field, worst first.

    A field the assessment did not reach is kept, as an unreported row: leaving it out would
    read as a field the feed does not need.
    """
    records = [
        {"Field": field_label(field), "Coverage": None if value is None else float(value)}
        for field, value in row.completeness.items()
    ]
    frame = pd.DataFrame(records, columns=["Field", "Coverage"])
    if frame.empty:
        return frame
    return frame.sort_values("Coverage", ascending=True, na_position="first").reset_index(
        drop=True
    )


def issue_lines(row: QualityRow) -> tuple[tuple[str, str], ...]:
    """The feed's errors then its warnings, each tagged with which it is."""
    return (
        *((status_label("error"), text) for text in row.errors),
        *((status_label("warning"), text) for text in row.warnings),
    )


def missing_field_lines(row: QualityRow) -> tuple[str, ...]:
    """`FacilityUse: activity, location` per opportunity type the assessment flagged."""
    return tuple(
        f"{kind}: {', '.join(fields)}" if fields else kind
        for kind, fields in sorted(row.missing_required_fields.items())
    )


# --- the summary charts -------------------------------------------------------------------

CHART_HEIGHT = 190
BAR_CORNER = 3


def _bar_chart(
    labels: Sequence[str],
    values: Sequence[float],
    colours: Sequence[str],
    *,
    label_title: str,
    value_title: str,
    label_colour: str,
    grid_colour: str,
    horizontal: bool = False,
    height: int = CHART_HEIGHT,
) -> alt.Chart | None:
    """One categorical bar chart, in the order the caller supplies.

    None for an empty series, matching `trend.sparkline_chart`, so the caller draws nothing
    rather than an empty axis. Colours arrive per bar and are used literally: which bar is
    red is a judgement about the data, and it belongs with the figures, not in the encoding.
    """
    if not labels:
        return None
    frame = pd.DataFrame(
        {"label": list(labels), "value": list(values), "colour": list(colours)}
    )
    category = alt.Axis(labelLimit=150, grid=False, labelAngle=0)
    measure = alt.Axis(tickCount=4, grid=True)
    encodings: dict[str, Any] = {
        "color": alt.Color("colour:N", scale=None, legend=None),
        "tooltip": [
            alt.Tooltip("label:N", title=label_title),
            alt.Tooltip("value:Q", title=value_title, format=",.1f"),
        ],
    }
    base = alt.Chart(frame)
    if horizontal:
        encodings["y"] = alt.Y("label:N", title=None, sort=None, axis=category)
        encodings["x"] = alt.X("value:Q", title=value_title, axis=measure)
        # The rounded end is the one the bar grows to, so it reads as a length from zero.
        mark = base.mark_bar(
            cornerRadiusTopRight=BAR_CORNER, cornerRadiusBottomRight=BAR_CORNER
        )
    else:
        encodings["x"] = alt.X("label:N", title=None, sort=None, axis=category)
        encodings["y"] = alt.Y("value:Q", title=value_title, axis=measure)
        mark = base.mark_bar(cornerRadiusTopLeft=BAR_CORNER, cornerRadiusTopRight=BAR_CORNER)

    chart: alt.Chart = (
        mark.encode(**encodings)
        .properties(height=height)
        .configure_view(strokeWidth=0, fill=None)
        .configure(background="transparent")
        .configure_axis(
            domain=False,
            labelColor=label_colour,
            titleColor=label_colour,
            tickColor=grid_colour,
            gridColor=grid_colour,
        )
    )
    return chart


def score_distribution_chart(
    summary: FeedQualitySummary,
    palette: Mapping[Tone, str],
    *,
    label_colour: str,
    grid_colour: str,
) -> alt.Chart | None:
    """How the scored feeds fall across the score bands, shaded on the score scale."""
    buckets = summary.score_buckets
    return _bar_chart(
        [bucket.label for bucket in buckets],
        [float(bucket.feed_count) for bucket in buckets],
        [palette[score_tone(bucket.lower)] for bucket in buckets],
        label_title="Score band",
        value_title="Feeds",
        label_colour=label_colour,
        grid_colour=grid_colour,
    )


def completeness_chart(
    summary: FeedQualitySummary,
    palette: Mapping[Tone, str],
    *,
    label_colour: str,
    grid_colour: str,
) -> alt.Chart | None:
    """Fleet-wide coverage per recommended field, worst last, shaded on the score scale."""
    reported = [
        (field, stat.average)
        for field, stat in summary.completeness.items()
        if stat.average is not None
    ]
    reported.sort(key=lambda item: item[1] or 0.0, reverse=True)
    return _bar_chart(
        [field_label(field) for field, _ in reported],
        [float(average or 0.0) for _, average in reported],
        [palette[score_tone(average)] for _, average in reported],
        label_title="Field",
        value_title="Mean coverage %",
        label_colour=label_colour,
        grid_colour=grid_colour,
        horizontal=True,
        height=260,
    )


def status_chart(
    summary: FeedQualitySummary,
    palette: Mapping[Tone, str],
    *,
    label_colour: str,
    grid_colour: str,
) -> alt.Chart | None:
    """Feeds per crawl status, shaded by the same status tones the table uses."""
    breakdown: Sequence[QualityBreakdown] = summary.status_breakdown
    return _bar_chart(
        [status_label(row.value) for row in breakdown],
        [float(row.feed_count) for row in breakdown],
        [palette[status_tone(row.value)] for row in breakdown],
        label_title="Status",
        value_title="Feeds",
        label_colour=label_colour,
        grid_colour=grid_colour,
    )


def grade_chart(
    summary: FeedQualitySummary,
    colour: str,
    *,
    label_colour: str,
    grid_colour: str,
) -> alt.Chart | None:
    """Feeds per quality grade. One colour: a grade is a band, not a RAG verdict, and
    shading Bronze red would claim the assessment did not."""
    breakdown: Sequence[QualityBreakdown] = summary.grade_breakdown
    return _bar_chart(
        [row.value.capitalize() or UNKNOWN for row in breakdown],
        [float(row.feed_count) for row in breakdown],
        [colour] * len(breakdown),
        label_title="Grade",
        value_title="Feeds",
        label_colour=label_colour,
        grid_colour=grid_colour,
    )
