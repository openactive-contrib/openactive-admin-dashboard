"""The activity coverage snapshot -> figures, a ranking chart, a district map and a table.

The fifth shaping module. Its read is three resources with no history: national figures, the
nationally ranked activities, and one row per local authority district and activity. None is
an incident (nothing ages), and the rows are not opportunities: a row counts the
opportunities in one district tagged with one activity, so an opportunity tagged with two
activities appears under both. A total across activities is therefore a count of activity
tags, and every figure here that sums across them says so rather than calling it
opportunities.

Pure, as `coverage.py` is: no Streamlit, no I/O, and the chart builder takes its colours
from the caller. The figures, the number formats and the card's verdict follow the Active
Places coverage monitor, which makes the same "measurement, not a fault queue" choice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import altair as alt
import pandas as pd

from stewards.api.models import ActivityCoverageSummary, ActivityDistrict, TopActivity
from stewards.geography import Boundaries
from stewards.monitors.choropleth import (
    Choropleth,
    ChoroplethStyle,
    TooltipField,
    choropleth,
    join,
)
from stewards.monitors.coverage import Stat, format_number, format_percent
from stewards.monitors.health import Health, HealthState, Movement
from stewards.monitors.overview import Fact, TileCard
from stewards.monitors.quality import BAR_CORNER
from stewards.monitors.registry import Monitor
from stewards.monitors.transforms import format_cell

#: What a blank categorical field reads as: a real string, so it is a value a steward can
#: select rather than an empty option.
UNKNOWN = "Unknown"

#: Sorts a row the batch reported no count for after every row that has one.
NO_FIGURE = -1.0

NOT_REPORTED = "activity coverage not reported in this snapshot"

#: The row key a district is joined to its boundary on.
DISTRICT_CODE = "district_code"

#: One bar's share of the ranking chart's height, so twenty bars are as legible as ten.
BAR_STEP = 26

#: Why a total across activities is not an opportunity count. Shown wherever one is drawn.
TAG_NOTE = (
    "Across all activities a local authority's figure counts activity tags: an opportunity "
    "tagged with two activities counts once under each."
)


# --- rows ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActivityRow:
    """One activity in one district, as the table shows it."""

    district_code: str
    district_name: str
    region_name: str
    country_name: str
    activity: str
    activity_id: str
    opportunity_count: int | None


def build_rows(districts: Sequence[ActivityDistrict]) -> tuple[ActivityRow, ...]:
    """One row per district and activity. A blank name reads as the district's code, then as
    "Unknown", so no row is an unnamed line in the table.

    ONS regions divide England only, so the batch sends no region for a Welsh, Scottish or
    Northern Irish district. Such a row takes its nation as its region — the level the ONS
    itself sets beside England's regions — rather than reading "Unknown".
    """
    return tuple(
        ActivityRow(
            district_code=row.district_code.strip(),
            district_name=row.district_name.strip() or row.district_code.strip() or UNKNOWN,
            region_name=row.region_name.strip() or row.country_name.strip() or UNKNOWN,
            country_name=row.country_name.strip() or UNKNOWN,
            activity=row.activity.strip() or UNKNOWN,
            activity_id=row.activity_id.strip(),
            opportunity_count=row.opportunity_count,
        )
        for row in districts
    )


def resolve(row: ActivityRow, field: str) -> Any:
    """Read a declared column or filter field off a row: every field is an attribute."""
    return getattr(row, field, None)


def sort_rows(monitor: Monitor, rows: Sequence[ActivityRow]) -> list[ActivityRow]:
    """The monitor's `sort_field` descending, then district, then activity. A row with no
    figure sorts after every row that has one, without reading as a zero."""

    def key(row: ActivityRow) -> tuple[float, str, str]:
        figure = resolve(row, monitor.sort_field)
        number = float(figure) if isinstance(figure, int | float) else NO_FIGURE
        return (-number, row.district_name.lower(), row.activity.lower())

    return sorted(rows, key=key)


def filter_options(rows: Sequence[ActivityRow], field: str) -> list[str]:
    """Distinct non-empty values for a filter field, sorted, for a selectbox."""
    return sorted(
        {str(value) for row in rows if (value := resolve(row, field)) not in (None, "")},
        key=str.lower,
    )


def search_rows(rows: Iterable[ActivityRow], term: str) -> list[ActivityRow]:
    """Case-insensitive substring match on the local authority's name. A blank term matches
    every row."""
    needle = term.strip().lower()
    if not needle:
        return list(rows)
    return [row for row in rows if needle in row.district_name.lower()]


def apply_filters(
    rows: Iterable[ActivityRow],
    *,
    search: str = "",
    selections: Mapping[str, str] | None = None,
    district_code: str | None = None,
) -> list[ActivityRow]:
    """The name search, per-field selections, then the district the map is centred on. No
    search, no selection and no district leave every row."""
    result = search_rows(rows, search)
    for field, wanted in (selections or {}).items():
        if wanted:
            result = [row for row in result if str(resolve(row, field) or "") == wanted]
    if district_code:
        result = [row for row in result if row.district_code == district_code]
    return result


def to_dataframe(monitor: Monitor, rows: Sequence[ActivityRow]) -> pd.DataFrame:
    """One line per row, columns in registry order; an empty frame for no rows."""
    labels = [col.label for col in monitor.columns]
    records = [
        {col.label: format_cell(col, resolve(row, col.field)) for col in monitor.columns}
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels)


def tone_frame(monitor: Monitor, rows: Sequence[ActivityRow]) -> pd.DataFrame:
    """Aligned with `to_dataframe` and blank throughout: a count of tags is not a state, so
    no cell is shaded."""
    labels = [col.label for col in monitor.columns]
    return pd.DataFrame([dict.fromkeys(labels, "") for _ in rows], columns=labels)


def district_label(rows: Iterable[ActivityRow], district_code: str | None) -> str | None:
    """The name the rows give a district code, the code itself where no row names it, and
    None for no district."""
    if not district_code:
        return None
    for row in rows:
        if row.district_code == district_code:
            return row.district_name
    return district_code


def table_caption(shown: int, total: int, *, district: str | None) -> str:
    """How many rows the table shows, and how to widen it again."""
    lead = (
        f"{shown:,} of {total:,} local authority and activity pairs shown, most "
        "opportunities first."
    )
    if district:
        return f"{lead} Narrowed to {district}: show the whole map to widen it again."
    return f"{lead} Filter to a local authority above the map to narrow it."


# --- the figures --------------------------------------------------------------------------


def page_stats(summary: ActivityCoverageSummary) -> tuple[Stat, ...]:
    """The three national figures above the chart. No tone: they are context, not a state."""
    return (
        Stat(
            "Opportunities with activity",
            format_number(summary.activity_opportunities),
            None,
            "name at least one activity",
        ),
        Stat(
            "Labelled opportunities",
            format_number(summary.labelled_opportunities),
            None,
            f"{format_percent(summary.labelled_percentage)} use an activity from the "
            "OpenActive activity list",
        ),
        Stat(
            "Activities offered",
            format_number(summary.activities_offered),
            None,
            f"of {format_number(summary.vocabulary_size)} in the activity list",
        ),
    )


def assess_activity_coverage(summary: ActivityCoverageSummary) -> Health:
    """The card's verdict: informational, as `coverage.assess_coverage` explains.

    An opportunity without a listed activity is not a fault a publisher is contacted about,
    and there is no defensible share to call good or bad, so the monitor is
    `Severity.INFORMATIONAL` and the warning renders as a grey "Info" chip. The movement is
    unknown: one snapshot says nothing about direction.
    """
    share = summary.labelled_percentage
    if share is None:
        return Health(HealthState.UNKNOWN, Movement.UNKNOWN, NOT_REPORTED)
    reason = (
        f"{format_percent(share)} of "
        f"{format_number(summary.activity_opportunities)} opportunities with an activity "
        "use the OpenActive activity list"
    )
    return Health(HealthState.WARNING, Movement.UNKNOWN, reason, current=share)


def tile_note(summary: ActivityCoverageSummary) -> str:
    if summary.labelled_percentage is None:
        return NOT_REPORTED
    return "UK-wide · this snapshot only"


def tile_card(summary: ActivityCoverageSummary) -> TileCard:
    """The overview card: the labelled share, three supporting figures and the verdict.

    `badge=None`: the sidebar pill counts what needs working through, and this is not a
    queue. The unit matches the registry's, which the overview also prints.
    """
    return TileCard(
        value=format_percent(summary.labelled_percentage),
        unit="of activity opportunities use the activity list",
        health=assess_activity_coverage(summary),
        facts=(
            Fact("With activity", format_number(summary.activity_opportunities)),
            Fact("Labelled", format_number(summary.labelled_opportunities)),
            Fact(
                "Activities",
                f"{format_number(summary.activities_offered)} of "
                f"{format_number(summary.vocabulary_size)}",
            ),
        ),
        note=tile_note(summary),
        badge=None,
    )


# --- the national ranking -----------------------------------------------------------------


def top_activity_chart(
    activities: Sequence[TopActivity],
    colour: str,
    *,
    label_colour: str,
    grid_colour: str,
) -> alt.Chart | None:
    """The nationally ranked activities as horizontal bars, first at the top.

    One flat colour, as `coverage.region_chart` uses: a count is a measurement, not a RAG
    verdict. An activity with no name or no count is left out rather than drawn as zero.
    None when nothing is left, so the caller draws a caption instead of an empty axis.
    """
    reported = [
        activity
        for activity in activities
        if activity.activity.strip() and activity.opportunity_count is not None
    ]
    if not reported:
        return None
    reported.sort(
        key=lambda a: (
            a.rank if a.rank is not None else float("inf"),
            -(a.opportunity_count or 0),
        )
    )
    frame = pd.DataFrame(
        {
            "label": [activity.activity.strip() for activity in reported],
            "value": [activity.opportunity_count or 0 for activity in reported],
            "districts": [activity.district_count for activity in reported],
        }
    )
    chart: alt.Chart = (
        alt.Chart(frame)
        .mark_bar(
            color=colour, cornerRadiusTopRight=BAR_CORNER, cornerRadiusBottomRight=BAR_CORNER
        )
        .encode(
            y=alt.Y(
                "label:N",
                title=None,
                sort=None,
                # Every activity labelled: Vega thins crowded band labels by default, which
                # would leave half the ranking unnamed.
                axis=alt.Axis(labelLimit=200, grid=False, labelAngle=0, labelOverlap=False),
            ),
            x=alt.X("value:Q", title="Opportunities", axis=alt.Axis(tickCount=4, grid=True)),
            tooltip=[
                alt.Tooltip("label:N", title="Activity"),
                alt.Tooltip("value:Q", title="Opportunities", format=","),
                alt.Tooltip("districts:Q", title="Local authorities", format=","),
            ],
        )
        .properties(height=BAR_STEP * len(reported))
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


# --- the district map ---------------------------------------------------------------------


def figure_label(activity: str | None) -> str:
    """What a district's figure is: opportunities for one activity, tags across them all."""
    return "Opportunities" if activity else "Activity tags"


def legend_title(activity: str | None) -> str:
    return f"{figure_label(activity)} (log scale)"


def district_map_rows(
    rows: Iterable[ActivityRow], activity: str | None = None
) -> list[dict[str, Any]]:
    """One plain row per district, keyed for `choropleth.join`: the opportunities tagged
    with `activity` there, or every activity tag when None.

    A row with no code is dropped: there is nothing to draw it on. A district whose rows
    report no count at all keeps a None figure, which the map draws as no data rather than
    zero.
    """
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.district_code or (activity and row.activity != activity):
            continue
        entry = totals.setdefault(
            row.district_code,
            {
                DISTRICT_CODE: row.district_code,
                "name": row.district_name,
                "region_name": row.region_name,
                "opportunity_count": None,
                "activity_count": 0,
            },
        )
        entry["activity_count"] += 1
        if row.opportunity_count is not None:
            so_far = entry["opportunity_count"] or 0
            entry["opportunity_count"] = so_far + row.opportunity_count
    return list(totals.values())


def district_tooltip(activity: str | None) -> tuple[TooltipField, ...]:
    """The hover, under the district's name."""
    fields = [
        TooltipField("region_name", "Region"),
        TooltipField("opportunity_count", figure_label(activity), ","),
    ]
    if not activity:
        fields.append(TooltipField("activity_count", "Activities", ","))
    return tuple(fields)


def district_map(
    rows: Sequence[ActivityRow],
    boundaries: Boundaries,
    style: ChoroplethStyle,
    *,
    activity: str | None = None,
    focus: str | None = None,
) -> Choropleth | None:
    """The UK's districts shaded by `activity`'s opportunities, or by every activity tag.

    On a log scale: district totals run from one to tens of thousands, and a linear ramp
    would leave all but a few cities at its pale end. None when no district reports a count.
    """
    return choropleth(
        boundaries,
        district_map_rows(rows, activity),
        code_key=DISTRICT_CODE,
        value_field="opportunity_count",
        tooltip=district_tooltip(activity),
        style=style,
        focus=focus,
        log_scale=True,
    )


def unmapped_districts(rows: Sequence[ActivityRow], boundaries: Boundaries) -> tuple[str, ...]:
    """Codes the rows report that no boundary carries — a boundary file from another year."""
    return join(boundaries, district_map_rows(rows), code_key=DISTRICT_CODE).unmatched


def map_note(
    rows: Sequence[ActivityRow], boundaries: Boundaries, activity: str | None = None
) -> str:
    """The tag caveat when the map sums across activities, and any district the map cannot
    draw, named rather than dropped silently."""
    parts = [] if activity else [TAG_NOTE]
    if missing := unmapped_districts(rows, boundaries):
        parts.append(
            f"Not on the map, as the boundaries carry no such code: {', '.join(missing)}."
        )
    return " ".join(parts)
