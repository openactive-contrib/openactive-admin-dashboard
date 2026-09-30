"""The whole of a schema drift monitor's page.

`quality_page.py` is this module's nearest neighbour: one read, a summary block beside the
rows, no history. The page is header, blurb, four figures, the most-used properties chart
where a trend would be, then two views of the snapshot — the feeds, with the selected
feed's properties, and the fleet-wide property table, with the feeds using the selected
property. Everything it shows was computed in `monitors.schema_drift`.

No email draft: a custom property is not a fault a publisher is contacted about, which is
also why the monitor is informational and carries no sidebar pill.

A thin renderer, as the hard rules require: no arithmetic here, and every feed column and
filter comes off the registry entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import altair as alt
import pandas as pd
import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import CustomPropertySummary
from stewards.components import layout, theme
from stewards.components.errors import render_api_error
from stewards.components.filters import ALL, FilterState
from stewards.components.incident_table import render_table
from stewards.components.loading import loading
from stewards.components.surface import card
from stewards.monitors import schema_drift, transforms
from stewards.monitors.registry import Monitor
from stewards.monitors.schema_drift import PropertyRow

#: A custom property is not a fault a publisher is contacted about, so this page drafts no
#: email and must not say it does.
NO_ACTIONS = "none"

PRESENCE = st.column_config.ProgressColumn(
    "Presence",
    min_value=0,
    max_value=100,
    format="%.0f%%",
    help="Share of the feed's sampled items carrying the property",
)


def render_blurb(monitor: Monitor) -> None:
    with card(f"blurb_{monitor.id}"):
        st.markdown(monitor.blurb)
        st.caption(" · ".join(f"`{chip}`" for chip in monitor.meta_chips))


def render_stats(monitor: Monitor, summary: CustomPropertySummary) -> None:
    """The snapshot's own figures, straight off the summary block.

    Deliberately not recomputed from the filtered table: they describe the fleet the batch
    analysed, and a filter narrowing the rows below must not appear to move them.
    """
    stats = schema_drift.page_stats(summary)
    for index, (column, stat) in enumerate(zip(st.columns(len(stats)), stats, strict=True)):
        with column, card(f"kpi_{monitor.id}_{index}"):
            layout.tone_metric(
                stat.label,
                stat.value,
                stat.tone,
                slug=f"{monitor.id}{index}",
                sub=stat.sub,
            )


def render_chart(title: str, caption: str, chart: alt.Chart | None, key: str) -> None:
    with card(key):
        st.subheader(title, anchor=False, divider=False)
        if chart is None:
            st.caption("This snapshot does not report the figures for this chart.")
            return
        st.altair_chart(chart, width="stretch")
        st.caption(caption)


def render_summary_chart(monitor: Monitor, summary: CustomPropertySummary) -> None:
    """Which custom properties are used most widely. Altair for the reason every other chart
    here uses it: a chosen colour and a transparent plot area."""
    render_chart(
        "Most used custom properties",
        f"Feeds carrying each property, the {schema_drift.CHART_LIMIT} most widely used. "
        "Every property is in the Properties tab below.",
        schema_drift.property_chart(
            summary,
            theme.TEAL,
            label_colour=theme.MUTED,
            grid_colour=theme.BORDER_SUBTLE,
        ),
        f"chart_{monitor.id}_properties",
    )


def render_filters(monitor: Monitor, rows: Sequence[PropertyRow]) -> FilterState:
    """Search, one selectbox per declared filter, and the outside-beta toggle.

    The toggle stands where a monitor page puts "past threshold only": nothing here ages, so
    the useful narrowing is to the feeds whose properties the beta namespace does not cover.
    """
    columns = st.columns([2, *([1] * len(monitor.filters)), 1], vertical_alignment="bottom")

    search = columns[0].text_input(
        "Search",
        key=f"search_{monitor.id}",
        placeholder="Filter publishers, datasets, feeds or properties…",
        label_visibility="collapsed",
    )

    selections: dict[str, str] = {}
    for column, spec in zip(columns[1:], monitor.filters, strict=False):
        options = [ALL, *schema_drift.filter_options(rows, spec.field)]
        chosen = column.selectbox(spec.label, options, key=f"filter_{monitor.id}_{spec.field}")
        selections[spec.field] = "" if chosen == ALL else chosen

    outside_only = columns[-1].toggle(
        "Outside beta only",
        value=False,
        key=f"outside_{monitor.id}",
        help="Show only feeds using a property with a publisher's own prefix or none.",
    )
    return FilterState(search=search, past_threshold_only=outside_only, selections=selections)


def render_feed_detail(monitor: Monitor, row: PropertyRow) -> None:
    """The selected feed's custom properties, one line per entity type."""
    with st.expander(f"Custom properties · {row.feed_name}", expanded=True):
        st.caption(
            f"{row.publisher_name} · {row.dataset_name} · {row.feed_type} · "
            f"{schema_drift.format_number(row.sampled_items)} items sampled"
        )
        frame = schema_drift.use_frame(row)
        if frame.empty:
            st.caption("This snapshot lists no custom properties for this feed.")
            return
        st.dataframe(
            frame,
            column_config={"Presence": PRESENCE},
            hide_index=True,
            width="stretch",
            key=f"uses_{monitor.id}",
        )


def render_feeds(monitor: Monitor, rows: Sequence[PropertyRow]) -> None:
    """Filters, the table of feeds and the selected feed."""
    state = render_filters(monitor, rows)
    shown = schema_drift.sort_rows(
        monitor,
        schema_drift.apply_filters(
            rows,
            search=state.search,
            selections=state.selections,
            outside_beta_only=state.past_threshold_only,
        ),
    )
    st.caption(
        f"{len(shown):,} of {len(rows):,} feeds shown, most custom properties first. Select "
        "a row to read the feed's properties."
    )
    selected = render_table(
        schema_drift.to_dataframe(monitor, shown),
        schema_drift.tone_frame(monitor, shown),
        columns=monitor.columns,
        rag_columns=transforms.rag_columns(monitor),
        key=f"table_{monitor.id}",
        empty_message=(
            "No feeds match these filters in this snapshot. Clear the filters to see every "
            "feed using a custom property."
        ),
    )
    if selected is not None and selected < len(shown):
        render_feed_detail(monitor, shown[selected])


def render_properties(
    monitor: Monitor, summary: CustomPropertySummary, rows: Sequence[PropertyRow]
) -> None:
    """Every custom property in the fleet, and the feeds using the selected one."""
    properties = schema_drift.sort_properties(summary)
    st.caption(
        f"{len(properties):,} custom properties, most widely used first. Select a row to see "
        "which feeds publish it."
    )
    frame = schema_drift.property_frame(properties)
    selected = render_table(
        frame,
        pd.DataFrame("", index=frame.index, columns=frame.columns),
        columns=schema_drift.PROPERTY_COLUMNS,
        rag_columns=[],
        key=f"properties_{monitor.id}",
        empty_message="This snapshot reports no property breakdown.",
    )
    if selected is None or selected >= len(properties):
        return
    name = properties[selected].property
    with st.expander(f"Feeds using · {name}", expanded=True):
        using = schema_drift.feeds_using(rows, name)
        if using.empty:
            st.caption("None of the feeds in this snapshot lists this property.")
            return
        st.dataframe(
            using,
            column_config={"Presence": PRESENCE},
            hide_index=True,
            width="stretch",
            key=f"using_{monitor.id}",
        )


def render_schema_drift_page(monitor: Monitor) -> None:
    """Header, blurb, figures, charts, the feeds and properties tabs, footer."""
    try:
        with loading("Loading custom properties"):
            response = repository.fetch_custom_properties(monitor.id)
    except ApiError as exc:
        layout.render_error_header(monitor.crumb, monitor.name)
        render_api_error(exc)
        return

    layout.render_header(monitor.crumb, monitor.name, response.meta)
    render_blurb(monitor)
    render_stats(monitor, response.summary)
    render_summary_chart(monitor, response.summary)

    rows = schema_drift.build_rows(response.data)
    feeds_tab, properties_tab = st.tabs(["Feeds", "Properties"])
    with feeds_tab:
        render_feeds(monitor, rows)
    with properties_tab:
        render_properties(monitor, response.summary, rows)

    layout.render_footer(monitor.query, actions=NO_ACTIONS)
