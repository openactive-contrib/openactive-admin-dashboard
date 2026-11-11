"""The whole of the schema compliance page.

`quality_page.py` and `schema_drift_page.py` are this module's neighbours, and the page is
deliberately their shape: header, blurb, five figures, one chart where a trend would be,
filters, the table, and the selected feed in a pop-up. What differs is the source. The page
has no endpoint of its own, so it reads the joined snapshot, and it dates the header from
the quality read — the one that decides which feeds are on the page.

No email draft: the registry entry declares no email fields, and a compliance verdict is a
reading of the feed against the standard rather than a fault that ages into a contact queue.

A thin renderer, as the hard rules require: no arithmetic here, and every column, filter and
label comes off the registry entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import altair as alt
import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.components import layout, theme
from stewards.components.errors import render_api_error
from stewards.components.filters import ALL, FilterState
from stewards.components.incident_table import render_table
from stewards.components.loading import loading
from stewards.components.row_dialog import render_row_dialog
from stewards.components.surface import card
from stewards.monitors import schema_compliance, transforms
from stewards.monitors.registry import Monitor
from stewards.monitors.schema_compliance import ComplianceRow
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

#: Said above the table when the custom-property read failed, so the blank cells in the
#: Custom properties column are not read as "this feed uses none".
PROPERTIES_UNAVAILABLE = (
    "The custom-property snapshot was not available, so the custom property counts are "
    "blank rather than zero."
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
        placeholder="Filter publishers, datasets or feeds…",
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
    """Why the selected feed got its verdict, which required fields it is missing, and its
    completeness per key field. Shown in the row pop-up."""
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


def render_schema_compliance_page(monitor: Monitor) -> None:
    """Header, blurb, figures, the key field chart, filters, table, selected feed, footer."""
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

    state = render_filters(monitor, rows)
    shown = schema_compliance.sort_rows(
        schema_compliance.apply_filters(
            rows,
            search=state.search,
            selections=state.selections,
            not_passing_only=state.past_threshold_only,
        )
    )

    unavailable = "" if snapshot.custom_properties is not None else f" {PROPERTIES_UNAVAILABLE}"
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

    layout.render_footer(monitor.query, actions=NO_ACTIONS)
