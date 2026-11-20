"""The custom-property snapshot -> rows, figures, charts and the property table.

No Streamlit, no I/O. This was the shaping behind a monitor of its own, the schema drift
page. That monitor is retired and the module outlived it: the compliance page reads the
same snapshot, so `monitors.schema_compliance` builds its rows from `build_row`, and the
page's Properties tab is `page_stats`, `property_chart`, `property_frame` and `feeds_using`.
The module keeps its name because the payload has not changed.

What it measures: which properties each feed publishes that the OpenActive vocabulary does
not define, and in which namespace. Nothing here is scored and nothing ages, so there is no
`first_detected`, no contact threshold and no RAG verdict to invent. The figures describe
the fleet the batch analysed and are read off the `summary` block rather than recomputed.

Pure, as `trend.py` and `quality.py` are: the chart builders take their colours from the
caller rather than importing the theme.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import altair as alt
import pandas as pd

from stewards.api.models import (
    CustomPropertyFeed,
    CustomPropertySummary,
    CustomPropertyUse,
    PropertyUsage,
)
from stewards.monitors.registry import Col, ColKind
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import EMPTY, format_cell

#: What a categorical field reads as when the snapshot did not report one. A real string
#: rather than a blank, so it is a filter option a steward can actually select.
UNKNOWN = "Unknown"

#: The OpenActive beta namespace: the sanctioned route for a term on its way into the
#: specification, as opposed to a publisher's own prefix or none at all.
BETA = "beta"

#: What a property with no namespace prefix reads as.
UNPREFIXED = "Unprefixed"

#: Sorts a row reporting no figure after every row that does, without conflating it with a
#: genuine zero.
NO_FIGURE = -1.0

#: Bars shown on the property chart. The full property list is the table
#: beside the feeds; a chart of 57 bars is a table drawn badly.
CHART_LIMIT = 15


# --- rows ---------------------------------------------------------------------------------


def namespace_label(namespace: str | None) -> str:
    """`beta` stays `beta`; a property with no prefix reads as unprefixed, not as blank."""
    return (namespace or "").strip() or UNPREFIXED


def _namespace_order(label: str) -> tuple[bool, bool, str]:
    """Beta first, then publisher prefixes alphabetically, then the unprefixed."""
    return (label != BETA, label == UNPREFIXED, label.lower())


def feed_label(feed: CustomPropertyFeed) -> str:
    """The feed's last URL segment — what a steward reads it by — falling back to its id."""
    path = urlparse(feed.feed_url).path.rstrip("/")
    segment = path.rsplit("/", 1)[-1] if path else ""
    return segment or feed.feed_id or UNKNOWN


def dataset_label(feed: CustomPropertyFeed) -> str:
    """The dataset's name, or its URL where the batch reported no name for it."""
    return feed.dataset_name.strip() or feed.dataset_url.strip() or UNKNOWN


@dataclass(frozen=True, slots=True)
class PropertyRow:
    """One feed as the table shows it: the snapshot's counts plus the few it derives."""

    publisher_id: str
    publisher_name: str
    dataset_name: str
    dataset_url: str
    feed_id: str
    feed_name: str
    feed_url: str
    feed_type: str
    sampled_items: int | None
    property_count: int | None
    usage_count: int | None

    #: Distinct properties with a publisher's own prefix or none — the part of the drift the
    #: beta namespace does not account for.
    outside_beta_count: int

    #: Every namespace, property name and entity type the feed uses, for the membership
    #: filters. A feed matches a namespace it uses anywhere.
    namespaces: tuple[str, ...]
    property_names: tuple[str, ...]
    entity_types: tuple[str, ...]

    uses: tuple[CustomPropertyUse, ...]

    @property
    def namespace_label(self) -> str:
        return ", ".join(self.namespaces) or EMPTY

    @property
    def property_label(self) -> str:
        """The feed's custom properties as one cell, alphabetically."""
        return ", ".join(self.property_names) or EMPTY

    @property
    def uses_outside_beta(self) -> bool:
        return self.outside_beta_count > 0


def _distinct(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}, key=str.lower))


def build_row(feed: CustomPropertyFeed) -> PropertyRow:
    """One feed's row.

    The two counts are the API's own figures. Where it sends null for one, the row reads the
    count off the property list it sent beside it rather than showing em dash for a figure
    the payload plainly carries — but only where that list is non-empty, because an empty
    list beside a null count is a figure not computed, not a zero.
    """
    uses = feed.custom_properties
    names = {use.property for use in uses if use.property}
    outside = {use.property for use in uses if use.property and use.namespace != BETA}
    property_count = feed.num_custom_properties
    usage_count = feed.num_custom_property_usages
    if uses:
        property_count = len(names) if property_count is None else property_count
        usage_count = len(uses) if usage_count is None else usage_count
    return PropertyRow(
        publisher_id=feed.publisher_id,
        publisher_name=feed.publisher_name or UNKNOWN,
        dataset_name=dataset_label(feed),
        dataset_url=feed.dataset_url,
        feed_id=feed.feed_id,
        feed_name=feed_label(feed),
        feed_url=feed.feed_url,
        feed_type=feed.feed_type.strip() or UNKNOWN,
        sampled_items=feed.sampled_items,
        property_count=property_count,
        usage_count=usage_count,
        outside_beta_count=len(outside),
        namespaces=tuple(
            sorted({namespace_label(use.namespace) for use in uses}, key=_namespace_order)
        ),
        property_names=_distinct(names),
        entity_types=_distinct(use.entity_type for use in uses),
        uses=uses,
    )


def build_rows(feeds: Sequence[CustomPropertyFeed]) -> tuple[PropertyRow, ...]:
    return tuple(build_row(feed) for feed in feeds)


# --- the figures --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stat:
    """One KPI block. `tone=None` leaves the value in body ink: every figure here is context
    rather than a state."""

    label: str
    value: str
    tone: Tone | None = None
    sub: str = ""


def format_number(count: int | None) -> str:
    """A count for display. None is a figure the batch did not report, never a zero."""
    return EMPTY if count is None else f"{count:,}"


def format_share(share: float | None) -> str:
    """A 0-1 share as a percentage."""
    return EMPTY if share is None else f"{share:.1%}"


def page_stats(summary: CustomPropertySummary) -> tuple[Stat, ...]:
    """The four figures above the table, straight off the snapshot's summary block."""
    return (
        Stat(
            "Datasets with custom properties",
            format_number(summary.datasets_with_custom_properties),
            sub=f"of {format_number(summary.datasets_assessed)} datasets assessed",
        ),
        Stat(
            "Feeds with custom properties",
            format_number(summary.feeds_with_custom_properties),
            sub=(
                f"{format_share(summary.feed_share)} of "
                f"{format_number(summary.feeds_assessed)} feeds assessed"
            ),
        ),
        Stat(
            "Distinct custom properties",
            format_number(summary.distinct_custom_properties),
            sub=(
                f"{format_number(summary.total_custom_property_usages)} usages across "
                f"{len(summary.namespace_breakdown)} namespaces"
            ),
        ),
        Stat(
            "Publishers with custom properties",
            format_number(summary.publishers_with_custom_properties),
            sub="publishing the feeds below",
        ),
    )


# --- the selected feed --------------------------------------------------------------------

USE_COLUMNS = ("Property", "Namespace", "Entity type", "Presence")


def use_frame(row: PropertyRow) -> pd.DataFrame:
    """The selected feed's custom properties, one line per entity type, most present first.

    A use whose presence was not reported is kept and sorts last: leaving it out would read
    as a property the feed does not use.
    """
    records = [
        {
            "Property": use.property or UNKNOWN,
            "Namespace": namespace_label(use.namespace),
            "Entity type": use.entity_type or UNKNOWN,
            "Presence": use.presence_pct,
        }
        for use in row.uses
    ]
    frame = pd.DataFrame(records, columns=list(USE_COLUMNS))
    if frame.empty:
        return frame
    return frame.sort_values(
        ["Presence", "Property"], ascending=[False, True], na_position="last"
    ).reset_index(drop=True)


# --- the property table -------------------------------------------------------------------

#: The fleet-wide property table, read off the summary block rather than the feed rows. It
#: is not the registry's `columns` because it is not the monitor's rows: it is a second view
#: of the snapshot, as the charts are, and its shape is fixed by the payload.
PROPERTY_COLUMNS: tuple[Col, ...] = (
    Col("property", "Property", ColKind.MONO, primary=True),
    Col("namespace", "Namespace", ColKind.TEXT),
    Col(
        "entity_types",
        "Entity types",
        ColKind.TEXT,
        help="Every entity type the property appears on, anywhere in the fleet",
    ),
    Col("feed_count", "Feeds", ColKind.NUMBER),
    Col("dataset_count", "Datasets", ColKind.NUMBER),
)


def sort_properties(summary: CustomPropertySummary) -> list[PropertyUsage]:
    """The most widely used first; a property reporting no feed count sorts last."""

    def key(usage: PropertyUsage) -> tuple[float, str]:
        feeds = usage.feed_count
        return (-(feeds if feeds is not None else NO_FIGURE), usage.property.lower())

    return sorted(summary.property_breakdown, key=key)


def _property_cell(usage: PropertyUsage, field: str) -> Any:
    """One `PROPERTY_COLUMNS` cell's raw value, with the labels the feed table uses."""
    match field:
        case "property":
            return usage.property or UNKNOWN
        case "namespace":
            return namespace_label(usage.namespace)
        case "entity_types":
            return ", ".join(usage.entity_types) or EMPTY
        case _:
            return getattr(usage, field, None)


def property_frame(properties: Sequence[PropertyUsage]) -> pd.DataFrame:
    """One line per property, in the order given, labelled as `PROPERTY_COLUMNS` declares."""
    labels = [col.label for col in PROPERTY_COLUMNS]
    records = [
        {
            col.label: format_cell(col, _property_cell(usage, col.field))
            for col in PROPERTY_COLUMNS
        }
        for usage in properties
    ]
    return pd.DataFrame(records, columns=labels)


FEEDS_USING_COLUMNS = ("Publisher", "Dataset", "Feed", "Entity type", "Presence")


def feeds_using(rows: Sequence[PropertyRow], property_name: str) -> pd.DataFrame:
    """Every feed use of one property, one line per entity type, most present first.

    Read off the feed rows, so it answers "who publishes this" for the selected property —
    the question the fleet-wide table raises and cannot answer by itself.
    """
    records = [
        {
            "Publisher": row.publisher_name,
            "Dataset": row.dataset_name,
            "Feed": row.feed_name,
            "Entity type": use.entity_type or UNKNOWN,
            "Presence": use.presence_pct,
        }
        for row in rows
        for use in row.uses
        if use.property == property_name
    ]
    frame = pd.DataFrame(records, columns=list(FEEDS_USING_COLUMNS))
    if frame.empty:
        return frame
    return frame.sort_values(
        ["Presence", "Publisher"], ascending=[False, True], na_position="last"
    ).reset_index(drop=True)


# --- the summary charts -------------------------------------------------------------------

CHART_HEIGHT = 190
BAR_CORNER = 3
BAR_STEP = 26


@dataclass(frozen=True, slots=True)
class _Bar:
    label: str
    feeds: int
    datasets: int | None


def _usage_chart(
    bars: Sequence[_Bar],
    colour: str,
    *,
    label_title: str,
    label_colour: str,
    grid_colour: str,
) -> alt.Chart | None:
    """Feeds per category as horizontal bars, in the order given.

    One flat colour, for the reason `coverage.region_chart` gives: how widely a property is
    used is a measurement, not a RAG verdict. None for an empty series, matching every other
    builder, so the caller draws nothing rather than an empty axis.
    """
    if not bars:
        return None
    frame = pd.DataFrame(
        {
            "label": [bar.label for bar in bars],
            "value": [bar.feeds for bar in bars],
            "datasets": [bar.datasets for bar in bars],
        }
    )
    tooltip = [
        alt.Tooltip("label:N", title=label_title),
        alt.Tooltip("value:Q", title="Feeds", format=","),
        alt.Tooltip("datasets:Q", title="Datasets", format=","),
    ]
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
                # Every bar is named: Vega otherwise drops labels it judges crowded, which
                # on a list of properties leaves every other bar anonymous.
                axis=alt.Axis(labelLimit=260, grid=False, labelAngle=0, labelOverlap=False),
            ),
            x=alt.X("value:Q", title="Feeds", axis=alt.Axis(tickCount=4, grid=True)),
            tooltip=tooltip,
        )
        .properties(height=max(CHART_HEIGHT, BAR_STEP * len(bars)))
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


def _widest[T](
    items: Iterable[T],
    feeds: Callable[[T], int | None],
    label: Callable[[T], str],
    limit: int,
) -> list[T]:
    """Items reporting a feed count and a label, most feeds first, cut to `limit`."""
    reported = [item for item in items if feeds(item) is not None and label(item).strip()]
    reported.sort(key=lambda item: (-(feeds(item) or 0), label(item).lower()))
    return reported[:limit]


def property_chart(
    summary: CustomPropertySummary,
    colour: str,
    *,
    label_colour: str,
    grid_colour: str,
    limit: int = CHART_LIMIT,
) -> alt.Chart | None:
    """The most widely used custom properties, by the feeds carrying them."""
    top = _widest(
        summary.property_breakdown, lambda u: u.feed_count, lambda u: u.property, limit
    )
    return _usage_chart(
        [_Bar(u.property, u.feed_count or 0, u.dataset_count) for u in top],
        colour,
        label_title="Property",
        label_colour=label_colour,
        grid_colour=grid_colour,
    )
