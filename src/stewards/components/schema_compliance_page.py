"""The whole of the schema compliance page.

`quality_page.py` is this module's nearest neighbour, and the page is deliberately its
shape: header, blurb, six figures, one chart where a trend would be, then two views of the
snapshot — the feeds, with the selected feed in a pop-up, and the fleet-wide custom
properties, with the feeds using the selected one. What differs is the source. The page has
no endpoint of its own, so it reads the joined snapshot, and it dates the header from the
quality read — the one that decides which feeds are on the page.

The Properties tab is the whole of what the retired schema drift page showed, and it reads
nothing extra: the custom-property snapshot arrives beside the quality one, summary block
and all, so the tab is a second view of a payload the page already holds.

No email draft: the registry entry declares no email fields, and a compliance verdict is a
reading of the feed against the standard rather than a fault that ages into a contact queue.

A thin renderer, as the hard rules require: no arithmetic here, and every column, filter and
label comes off the registry entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import altair as alt
import pandas as pd
import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import CustomPropertyResponse, CustomPropertySummary
from stewards.components import layout, theme
from stewards.components.errors import render_api_error
from stewards.components.filters import ALL, FilterState
from stewards.components.incident_table import render_table
from stewards.components.loading import loading
from stewards.components.row_dialog import render_row_dialog
from stewards.components.surface import card
from stewards.monitors import schema_compliance, schema_drift, transforms
from stewards.monitors.registry import Monitor
from stewards.monitors.schema_compliance import ComplianceRow
from stewards.monitors.schema_drift import PropertyRow
from stewards.monitors.thresholds import status_label, status_tone

#: The registry entry declares no email fields, so the footer must not claim a draft.
NO_ACTIONS = "none"

COMPLETENESS = st.column_config.ProgressColumn(
    "Completeness",
    min_value=0,
    max_value=100,
    format="%.0f%%",
    help="Share of the feed's sampled items carrying the field",
)

VOCABULARY_MATCH = st.column_config.ProgressColumn(
    "Match",
    min_value=0,
    max_value=100,
    format="%.0f%%",
    help="Share of the feed's sampled terms matching the standard vocabulary",
)

PRESENCE = st.column_config.ProgressColumn(
    "Presence",
    min_value=0,
    max_value=100,
    format="%.0f%%",
    help="Share of the feed's sampled items carrying the property",
)

#: Said above the table when the custom-property read failed, so the blank cells in the
#: Custom properties column are not read as "this feed uses none".
PROPERTIES_UNAVAILABLE = (
    "The custom-property snapshot was not available, so the custom property counts are "
    "blank rather than zero."
)

#: The Properties tab when that same read failed. It has nothing to draw at all, so it says
#: so rather than rendering an empty table that reads as "the fleet uses none".
NO_PROPERTIES_SNAPSHOT = (
    "The custom-property snapshot was not available, so this tab has nothing to show. The "
    "compliance assessment on the Feeds tab is unaffected."
)

#: The pop-up's own custom-property section when that read failed. Distinct from a feed that
#: publishes none, which `ComplianceRow.properties_reported` is what tells apart.
NO_FEED_PROPERTIES_SNAPSHOT = (
    "The custom-property snapshot was not available, so this feed's properties could not be "
    "listed."
)

#: The same section for a feed the snapshot does cover and found nothing on.
NO_FEED_PROPERTIES = (
    "This feed publishes no property the OpenActive vocabulary does not define."
)


def render_blurb(monitor: Monitor) -> None:
    with card(f"blurb_{monitor.id}"):
        st.markdown(monitor.blurb)
        st.caption(" · ".join(f"`{chip}`" for chip in monitor.meta_chips))


def render_stats(monitor: Monitor, rows: Sequence[ComplianceRow]) -> None:
    """The figures above the table.

    Worked out from every row rather than the filtered ones, for the reason the quality page
    gives: they describe the fleet the batch assessed, and a filter narrowing the table below
    must not appear to move them.
    """
    stats = schema_compliance.page_stats(rows)
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


def render_summary_chart(monitor: Monitor, rows: Sequence[ComplianceRow]) -> None:
    """How well the fleet covers each key field. It stands where the trend chart would, for
    the reason the quality page gives: there is no history behind this snapshot."""
    render_chart(
        "Key field completeness",
        "Mean completeness of each key recommended field across the feeds that reported it, "
        "shaded on the same scale as the field columns below.",
        schema_compliance.key_field_chart(
            rows,
            theme.FOREGROUND,
            label_colour=theme.MUTED,
            grid_colour=theme.BORDER_SUBTLE,
        ),
        f"chart_{monitor.id}_key_fields",
    )


def render_filters(monitor: Monitor, rows: Sequence[ComplianceRow]) -> FilterState:
    """Search, one selectbox per declared filter, and the compliance toggle.

    The toggle stands where a monitor page puts "past threshold only": nothing here ages, so
    the useful narrowing is to the feeds that do not meet the standard.
    """
    columns = st.columns([2, *([1] * len(monitor.filters)), 1], vertical_alignment="bottom")

    search = columns[0].text_input(
        "Search",
        key=f"search_{monitor.id}",
        placeholder="Filter publishers, datasets, feeds, missing fields or properties…",
        label_visibility="collapsed",
    )

    selections: dict[str, str] = {}
    for column, spec in zip(columns[1:], monitor.filters, strict=False):
        options = [ALL, *schema_compliance.filter_options(rows, spec.field)]
        chosen = column.selectbox(spec.label, options, key=f"filter_{monitor.id}_{spec.field}")
        selections[spec.field] = "" if chosen == ALL else chosen

    not_passing_only = columns[-1].toggle(
        "Not passing only",
        value=False,
        key=f"not_passing_{monitor.id}",
        help="Show only feeds that fail the standard or fall short of the key fields.",
    )
    return FilterState(
        search=search, past_threshold_only=not_passing_only, selections=selections
    )


def render_row_detail(monitor: Monitor, row: ComplianceRow) -> None:
    """Why the selected feed got its verdict, which required fields it is missing, its
    completeness per key field, how its terms matched the vocabularies, and which properties
    it publishes outside them. Shown in the row pop-up."""
    st.caption(
        f"{row.publisher_name} · {row.dataset_name} · {row.feed_type} · "
        f"version {row.feed_version}"
    )
    st.badge(status_label(row.status), color=theme.markdown_colour(status_tone(row.status)))

    if reasons := row.reasons:
        for reason in reasons:
            st.markdown(f"- {reason}")
    else:
        st.markdown("Every required field is present and the key fields are well covered.")

    if missing := schema_compliance.missing_field_lines(row):
        st.markdown("**Missing required fields**")
        for line in missing:
            st.markdown(f"- {line}")

    st.dataframe(
        schema_compliance.key_field_frame(row),
        column_config={"Completeness": COMPLETENESS},
        hide_index=True,
        width="stretch",
        key=f"key_fields_{monitor.id}",
    )

    render_vocabularies(monitor, row)
    render_feed_properties(monitor, row)


def render_vocabularies(monitor: Monitor, row: ComplianceRow) -> None:
    """How the feed's activity and facility terms matched the standard vocabularies."""
    st.markdown("**Vocabulary match**")
    vocabularies = schema_compliance.vocabulary_frame(row)
    if vocabularies.empty:
        st.caption(schema_compliance.NO_VOCABULARY_FIGURES)
        return
    st.dataframe(
        vocabularies,
        column_config={"Match": VOCABULARY_MATCH},
        hide_index=True,
        width="stretch",
        key=f"vocabularies_{monitor.id}",
    )
    render_unmatched_terms(row)


def render_unmatched_terms(row: ComplianceRow) -> None:
    """The terms the assessment could not match, as the feed published them.

    The counts above say how badly a feed did; this says what to fix, so it is the part a
    steward quotes in an email. Drawn in a code block rather than markdown deliberately:
    these are free-form publisher strings, and an asterisk or a backtick in one would come
    out as formatting instead of the term.
    """
    found = schema_compliance.unmatched_terms(row)
    if not found:
        return
    st.markdown("**Terms that did not match**")
    for label, terms in found:
        if not terms:
            st.caption(f"{label} · {schema_compliance.NONE_NAMED}")
            continue
        st.caption(f"{label} · {len(terms)} of the unmatched terms")
        st.code("\n".join(terms), language=None)


def render_feed_properties(monitor: Monitor, row: ComplianceRow) -> None:
    """The feed's own custom properties, one line per entity type.

    The blurb promises this, and the Properties tab does not answer it: that tab is the
    fleet's properties and which feeds carry each one, read off the summary block. This is
    the other direction — one feed, every property it publishes.

    A feed the snapshot covers and found nothing on, and a snapshot that never arrived, are
    not the same statement, so they do not share a message.
    """
    st.markdown("**Custom properties**")
    if not row.properties_reported:
        st.caption(NO_FEED_PROPERTIES_SNAPSHOT)
        return
    frame = None if row.properties is None else schema_drift.use_frame(row.properties)
    if frame is None or frame.empty:
        st.caption(NO_FEED_PROPERTIES)
        return
    st.dataframe(
        frame,
        column_config={"Presence": PRESENCE},
        hide_index=True,
        width="stretch",
        key=f"uses_{monitor.id}",
    )


def render_feeds(
    monitor: Monitor, rows: Sequence[ComplianceRow], *, properties_reported: bool
) -> None:
    """Filters, the table of assessed feeds and the selected feed."""
    state = render_filters(monitor, rows)
    shown = schema_compliance.sort_rows(
        schema_compliance.apply_filters(
            rows,
            search=state.search,
            selections=state.selections,
            not_passing_only=state.past_threshold_only,
        )
    )

    unavailable = "" if properties_reported else f" {PROPERTIES_UNAVAILABLE}"
    st.caption(
        f"{len(shown):,} of {len(rows):,} feeds shown, least compliant first. Select a row "
        f"to open it.{unavailable}"
    )
    selected = render_table(
        schema_compliance.to_dataframe(monitor.columns, shown),
        schema_compliance.tone_frame(monitor.columns, shown),
        columns=monitor.columns,
        rag_columns=transforms.rag_columns(monitor),
        key=f"table_{monitor.id}",
        empty_message=(
            "No feeds match these filters in this snapshot. Clear the filters to see every "
            "assessed feed."
        ),
    )
    render_row_dialog(
        shown,
        lambda row: f"{row.dataset_key}:{row.feed_id}",
        selected,
        key=f"dialog_{monitor.id}",
        table_key=f"table_{monitor.id}",
        title=lambda row: f"Compliance · {row.feed_name}",
        render=lambda row: render_row_detail(monitor, row),
    )


def render_property_stats(monitor: Monitor, summary: CustomPropertySummary) -> None:
    """The Properties tab's own figures, straight off the custom-property summary block.

    Deliberately not recomputed from the table below, for the reason `render_stats` gives:
    they describe the fleet the batch analysed. They are also not the page's headline
    figures, which is why they sit inside the tab rather than above it — nothing a publisher
    is contacted about, and nothing that moves the compliance verdict.
    """
    stats = schema_drift.page_stats(summary)
    for index, (column, stat) in enumerate(zip(st.columns(len(stats)), stats, strict=True)):
        with column, card(f"propkpi_{monitor.id}_{index}"):
            layout.tone_metric(
                stat.label,
                stat.value,
                stat.tone,
                slug=f"{monitor.id}prop{index}",
                sub=stat.sub,
            )


def render_property_chart(monitor: Monitor, summary: CustomPropertySummary) -> None:
    """Which custom properties are used most widely."""
    render_chart(
        "Most used custom properties",
        f"Feeds carrying each property, the {schema_drift.CHART_LIMIT} most widely used. "
        "Every property is in the table below.",
        schema_drift.property_chart(
            summary,
            theme.TEAL,
            label_colour=theme.MUTED,
            grid_colour=theme.BORDER_SUBTLE,
        ),
        f"chart_{monitor.id}_properties",
    )


def render_property_table(
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


def render_properties(monitor: Monitor, properties: CustomPropertyResponse | None) -> None:
    """The Properties tab: the fleet's custom properties, or why there are none to show."""
    if properties is None:
        st.caption(NO_PROPERTIES_SNAPSHOT)
        return
    render_property_stats(monitor, properties.summary)
    render_property_chart(monitor, properties.summary)
    render_property_table(monitor, properties.summary, schema_drift.build_rows(properties.data))


def render_schema_compliance_page(monitor: Monitor) -> None:
    """Header, blurb, figures, the key field chart, the two tabs, footer."""
    try:
        with loading("Loading the compliance snapshot"):
            snapshot = repository.fetch_schema_compliance()
    except ApiError as exc:
        layout.render_error_header(monitor.crumb, monitor.name)
        render_api_error(exc)
        return

    # The quality read decides which feeds are on the page, so its snapshot is the one the
    # header dates. See `monitors.schema_compliance`.
    layout.render_header(monitor.crumb, monitor.name, snapshot.quality.meta)
    render_blurb(monitor)

    rows = schema_compliance.build_rows(snapshot)
    render_stats(monitor, rows)
    render_summary_chart(monitor, rows)

    feeds_tab, properties_tab = st.tabs(["Feeds", "Properties"])
    with feeds_tab:
        render_feeds(monitor, rows, properties_reported=snapshot.custom_properties is not None)
    with properties_tab:
        render_properties(monitor, snapshot.custom_properties)

    layout.render_footer(monitor.query, actions=NO_ACTIONS)
