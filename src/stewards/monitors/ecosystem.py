"""The ecosystem growth series -> table rows and a chart. No Streamlit, no I/O.

Not a monitor: the growth page reads one row per calendar month describing the size of the
ecosystem, judges nothing and has no card on the overview. It still follows the rules the
monitors do — every value the page shows is computed here, and the chart builder takes its
colours from the caller, as `trend.py` does.

A figure the batch did not compute for a month is null. It stays blank in the table and is
left out of the chart rather than drawn as a zero.

Most figures also name the datasets behind them. The table keeps to counts so it stays
readable; selecting a month opens its lists in a pop-up, one tab per list, built from the
helpers at the end of this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import altair as alt
import pandas as pd

from stewards.api.models import EcosystemGrowthPeriod, GrowthDataset
from stewards.monitors.registry import Col, ColKind
from stewards.monitors.transforms import EMPTY

BLURB = (
    "How the OpenActive ecosystem changes month by month: how many datasets the crawl "
    "reached, how many joined or left, how many the pipeline could not ingest, how many "
    "still carry opportunities starting in the future, and how many stopped publishing. "
    "Each month is built from the daily snapshots "
    "that fall inside it, so a month with few days of data describes less than a full month, "
    "and the current month is still in progress until it ends."
)

IN_PROGRESS = "In progress"
COMPLETE = "Complete"

MONTH = "Month"
STATUS = "Status"
DAYS_WITH_DATA = "Days with data"

PUBLISHER = "Publisher"
DATASET = "Dataset"
URL = "URL"

NOT_COMPUTED = "Not computed for this month."
NONE_THIS_MONTH = "None this month."


@dataclass(frozen=True, slots=True)
class Series:
    """One figure the chart can draw: a field on the period, its label, and its line style.

    `list_field` names the period's list of the datasets behind the figure, where the API
    sends one. `in_table` False keeps a figure to the chart. `help` is the column tooltip,
    worded from the API's own definition of the figure.
    """

    field: str
    label: str
    dashed: bool = False
    list_field: str | None = None
    in_table: bool = True
    help: str | None = None


#: Every figure, in picker order; the table shows those with `in_table`. A dashed line tells
#: a series apart from the one drawn in the same colour beside it — added from crawled, not
#: ingested from without future, the failed subset from stopped — so the chart needs no
#: colour the palette does not already have.
SERIES: tuple[Series, ...] = (
    Series(
        "datasets_crawled",
        "Datasets crawled",
        help="Listed by a catalogue on at least one crawl in the month",
    ),
    Series(
        "datasets_added",
        "Added",
        dashed=True,
        list_field="datasets_added_list",
        help="Listed this month, not the month before",
    ),
    Series(
        "datasets_removed",
        "Removed",
        dashed=True,
        list_field="datasets_removed_list",
        help="Listed the month before, not this month",
    ),
    Series("datasets_with_future", "With future", in_table=False),
    Series(
        "datasets_without_future",
        "Without future",
        list_field="datasets_without_future_list",
        help="Ingested, with no future opportunities on any feed on any day",
    ),
    Series(
        "datasets_not_ingested",
        "Not ingested",
        dashed=True,
        list_field="datasets_not_ingested_list",
        help="Listed, but none of its feeds was ingested all month",
    ),
    Series(
        "datasets_stopped",
        "Stopped",
        list_field="datasets_stopped_list",
        help="Ingested, with no updates or deletes all month",
    ),
    Series(
        "datasets_stopped_all_runs_failed",
        "Stopped (all runs failed)",
        dashed=True,
        list_field="datasets_stopped_all_runs_failed_list",
        help="Stopped, with every feed run ending in an error",
    ),
)

#: The figures the monthly table shows, in order.
TABLE_SERIES: tuple[Series, ...] = tuple(s for s in SERIES if s.in_table)

#: The series whose datasets the month pop-up lists, one tab each, in table order.
LISTED_SERIES: tuple[Series, ...] = tuple(s for s in SERIES if s.list_field is not None)

#: What the picker selects on first load: the size of the ecosystem, the part of it that is
#: useful to a consumer, and the part that has gone quiet.
DEFAULT_SERIES: tuple[str, ...] = (
    "datasets_crawled",
    "datasets_with_future",
    "datasets_stopped",
)

_BY_FIELD: Mapping[str, Series] = {s.field: s for s in SERIES}

TABLE_COLUMNS: tuple[Col, ...] = (
    Col("month", MONTH, ColKind.MONO, primary=True),
    Col("status", STATUS, ColKind.TEXT),
    Col(
        "days_with_data",
        DAYS_WITH_DATA,
        ColKind.NUMBER,
        help="Daily snapshots that fall inside the month",
    ),
    *(Col(s.field, s.label, ColKind.NUMBER, help=s.help) for s in TABLE_SERIES),
)

CHART_HEIGHT = 280


def series_for(field: str) -> Series:
    """The series a field draws. An unknown field is a programming error, not a data one."""
    try:
        return _BY_FIELD[field]
    except KeyError as exc:
        raise KeyError(f"unknown growth series {field!r}") from exc


def label_for(field: str) -> str:
    return series_for(field).label


def month_label(period: EcosystemGrowthPeriod) -> str:
    """The ISO month a period covers, e.g. `2026-11`."""
    return period.period_start.strftime("%Y-%m")


def status_label(period: EcosystemGrowthPeriod) -> str:
    return IN_PROGRESS if period.in_progress else COMPLETE


def oldest_first(periods: Iterable[EcosystemGrowthPeriod]) -> list[EcosystemGrowthPeriod]:
    return sorted(periods, key=lambda p: p.period_start)


def newest_first(periods: Iterable[EcosystemGrowthPeriod]) -> list[EcosystemGrowthPeriod]:
    return sorted(periods, key=lambda p: p.period_start, reverse=True)


def table_frame(periods: Sequence[EcosystemGrowthPeriod]) -> pd.DataFrame:
    """One row per month, most recent first, with every figure marked `in_table`.

    Counts are nullable integers, so a month the batch did not compute renders blank rather
    than as zero, and a reported zero still reads 0.
    """
    labels = [col.label for col in TABLE_COLUMNS]
    rows = [
        {
            MONTH: month_label(period),
            STATUS: status_label(period),
            DAYS_WITH_DATA: period.days_with_data,
            **{s.label: getattr(period, s.field) for s in TABLE_SERIES},
        }
        for period in newest_first(periods)
    ]
    frame = pd.DataFrame(rows, columns=labels)
    counts = [DAYS_WITH_DATA, *(s.label for s in TABLE_SERIES)]
    return frame.astype(dict.fromkeys(counts, "Int64"))


def chart_frame(
    periods: Sequence[EcosystemGrowthPeriod], fields: Sequence[str]
) -> pd.DataFrame:
    """Long frame for the chart, oldest month first: one row per month per chosen series.

    A null figure has no row, so the chart omits the point rather than plotting a zero.
    """
    chosen = [series_for(field) for field in fields]
    rows = [
        {
            "month": month_label(period),
            "series": series.label,
            "value": value,
            "status": status_label(period),
            "in_progress": period.in_progress,
        }
        for period in oldest_first(periods)
        for series in chosen
        if (value := getattr(period, series.field)) is not None
    ]
    return pd.DataFrame(rows, columns=["month", "series", "value", "status", "in_progress"])


def growth_chart(
    periods: Sequence[EcosystemGrowthPeriod],
    fields: Sequence[str],
    *,
    colours: Mapping[str, str],
    label_colour: str,
    grid_colour: str,
    surface_colour: str,
    height: int = CHART_HEIGHT,
) -> alt.LayerChart | None:
    """One line per chosen series, with a point per month.

    A month still in progress gets a hollow point — filled with the card surface — so a
    partial figure is not read as a complete one. Altair rather than `st.line_chart` for the
    reasons `trend.trend_chart` gives: dashed lines and a transparent plot area.

    None when there is nothing to draw: no periods, no chosen series, or only nulls.
    """
    frame = chart_frame(periods, fields)
    if frame.empty:
        return None
    chosen = [series_for(field) for field in fields]
    domain = [s.label for s in chosen]
    colour_scale = alt.Scale(domain=domain, range=[colours[s.field] for s in chosen])
    months = [month_label(p) for p in oldest_first(periods)]

    base = alt.Chart(frame).encode(
        x=alt.X(
            "month:O",
            title=None,
            sort=months,
            axis=alt.Axis(labelAngle=0, labelOverlap=True, grid=False),
        ),
        y=alt.Y(
            "value:Q",
            title=None,
            scale=alt.Scale(zero=True, nice=True),
            axis=alt.Axis(tickCount=5, grid=True, format=",d"),
        ),
    )
    lines = base.mark_line(strokeWidth=2).encode(
        color=alt.Color(
            "series:N",
            title=None,
            scale=colour_scale,
            legend=alt.Legend(orient="bottom", direction="horizontal", symbolType="stroke"),
        ),
        strokeDash=alt.StrokeDash(
            "series:N",
            title=None,
            scale=alt.Scale(
                domain=domain, range=[[4, 3] if s.dashed else [1, 0] for s in chosen]
            ),
            legend=None,
        ),
    )
    points = base.mark_point(size=60, strokeWidth=2, opacity=1, fillOpacity=1).encode(
        stroke=alt.Color("series:N", scale=colour_scale, legend=None),
        fill=alt.condition(
            "datum.in_progress",
            alt.value(surface_colour),
            alt.Color("series:N", scale=colour_scale, legend=None),
        ),
        tooltip=[
            alt.Tooltip("month:O", title="Month"),
            alt.Tooltip("series:N", title="Series"),
            alt.Tooltip("value:Q", title="Datasets", format=",d"),
            alt.Tooltip("status:N", title="Status"),
        ],
    )
    chart: alt.LayerChart = (
        alt.layer(lines, points)
        .properties(height=height)
        .configure_view(strokeWidth=0, fill=None)
        .configure(background="transparent")
        .configure_axis(
            domain=False,
            labelColor=label_colour,
            tickColor=grid_colour,
            gridColor=grid_colour,
        )
    )
    return chart


# --- the month pop-up ---------------------------------------------------------------------

#: The columns of one list in the pop-up. The link shows the URL's host: several datasets
#: share a name like "Sessions and Facilities", and the host is what tells them apart.
#: `LinkColumn` reads a regex `display_text` as "show the first capture group".
DETAIL_COLUMNS: tuple[Col, ...] = (
    Col("publisher", PUBLISHER, ColKind.TEXT, primary=True),
    Col("dataset", DATASET, ColKind.TEXT),
    Col("url", URL, ColKind.LINK, link_text=r"https?://([^/]+)"),
)


def dataset_list(
    period: EcosystemGrowthPeriod, series: Series
) -> tuple[GrowthDataset, ...] | None:
    """The datasets behind one figure for a month. None where the batch did not compute it,
    and for a series with no list at all."""
    if series.list_field is None:
        return None
    datasets: tuple[GrowthDataset, ...] | None = getattr(period, series.list_field)
    return datasets


def _count_text(value: int | None) -> str:
    return EMPTY if value is None else f"{value:,}"


def list_tab_label(period: EcosystemGrowthPeriod, series: Series) -> str:
    """The tab for one list, carrying the month's count: `Added · 2`, `Added · —`."""
    return f"{series.label} · {_count_text(getattr(period, series.field))}"


def dialog_title(period: EcosystemGrowthPeriod) -> str:
    return f"{month_label(period)} · Datasets"


def dialog_caption(period: EcosystemGrowthPeriod) -> str:
    """How much of the month the lists describe."""
    return f"{status_label(period)} · {_count_text(period.days_with_data)} days with data"


def detail_frame(datasets: Sequence[GrowthDataset]) -> pd.DataFrame:
    """One row per dataset, sorted by publisher then dataset name, ignoring case.

    Names are trimmed (the catalogue sends some with a leading space), and a publisher the
    catalogue could not identify reads as an em dash rather than a blank or `pub_unknown`.
    """
    rows = sorted(
        (
            {
                PUBLISHER: dataset.publisher_name.strip() or EMPTY,
                DATASET: dataset.dataset_name.strip() or EMPTY,
                URL: dataset.dataset_url,
            }
            for dataset in datasets
        ),
        key=lambda row: (row[PUBLISHER].casefold(), row[DATASET].casefold(), row[URL]),
    )
    return pd.DataFrame(rows, columns=[col.label for col in DETAIL_COLUMNS])
