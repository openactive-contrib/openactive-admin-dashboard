"""The whole of a coverage monitor's page.

`quality_page.py` is this module's nearest neighbour: same shape, read differently. A
coverage run reports how much of an external estate the fleet reaches — four figures from one
endpoint, the site-venue pairs behind them from a second — so the page is header, blurb, four
figures, the regional chart where a trend would be, filters, the table of pairs, and the
selected pair's OpenActive side. Everything it shows was computed in `monitors.coverage`.

No email draft: a coverage gap is not a fault a publisher is contacted about, which is also
why the monitor is informational and carries no sidebar pill.

A thin renderer, as the hard rules require: no arithmetic here, and every column, filter and
label comes off the registry entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import CoverageSnapshot
from stewards.components import layout, theme
from stewards.components.errors import render_api_error
from stewards.components.filters import ALL, FilterState
from stewards.components.incident_table import render_table
from stewards.components.surface import card
from stewards.monitors import coverage, transforms
from stewards.monitors.coverage import MappingRow
from stewards.monitors.registry import Monitor

#: A coverage gap is not a fault a publisher is contacted about, so this page drafts no
#: email and must not say it does.
NO_ACTIONS = "none"


def render_blurb(monitor: Monitor) -> None:
    with card(f"blurb_{monitor.id}"):
        st.markdown(monitor.blurb)
        st.caption(" · ".join(f"`{chip}`" for chip in monitor.meta_chips))


def render_stats(monitor: Monitor, snapshot: CoverageSnapshot) -> None:
    """The run's own figures, straight off the snapshot's headline.

    Deliberately not recomputed from the filtered table: they describe the whole estate the
    batch measured, and a filter narrowing the pairs below must not appear to move them.
    """
    stats = coverage.page_stats(snapshot)
    for index, (column, stat) in enumerate(zip(st.columns(len(stats)), stats, strict=True)):
        with column, card(f"kpi_{monitor.id}_{index}"):
            layout.tone_metric(
                stat.label,
                stat.value,
                stat.tone,
                slug=f"{monitor.id}{index}",
                sub=stat.sub,
            )


def render_summary_chart(monitor: Monitor, snapshot: CoverageSnapshot) -> None:
    """Coverage by region: where in England the OpenActive data reaches the estate.

    Altair rather than `st.bar_chart` for the reason every other chart here uses it: a
    transparent plot area and a chosen colour, so the chart sits on the white card rather
    than in a grey panel.
    """
    chart = coverage.region_chart(
        snapshot,
        theme.TEAL,
        label_colour=theme.MUTED,
        grid_colour=theme.BORDER_SUBTLE,
    )
    with card(f"chart_{monitor.id}_region"):
        st.subheader("Coverage by region", anchor=False, divider=False)
        if chart is None:
            st.caption("This snapshot does not report a regional breakdown.")
            return
        st.altair_chart(chart, width="stretch")
        st.caption(
            "The share of Active Places sites in each English region that an OpenActive "
            "venue matches, on any of the three channels."
        )


def render_filters(monitor: Monitor, rows: Sequence[MappingRow]) -> FilterState:
    """Search, one selectbox per declared filter, and the primary-pairs toggle.

    The toggle stands where a monitor page puts "past threshold only": nothing in a coverage
    snapshot ages, so the useful narrowing is to one pair per venue.
    """
    columns = st.columns([2, *([1] * len(monitor.filters)), 1], vertical_alignment="bottom")

    search = columns[0].text_input(
        "Search",
        key=f"search_{monitor.id}",
        placeholder="Filter sites, postcodes, authorities, venues or publishers…",
        label_visibility="collapsed",
    )

    selections: dict[str, str] = {}
    for column, spec in zip(columns[1:], monitor.filters, strict=False):
        options = [ALL, *coverage.filter_options(rows, spec.field)]
        chosen = column.selectbox(spec.label, options, key=f"filter_{monitor.id}_{spec.field}")
        selections[spec.field] = "" if chosen == ALL else chosen

    primary_only = columns[-1].toggle(
        "Primary pairs only",
        value=False,
        key=f"primary_{monitor.id}",
        help="Show only the site each OpenActive venue is primarily matched to.",
    )
    return FilterState(search=search, past_threshold_only=primary_only, selections=selections)


def render_row_detail(row: MappingRow) -> None:
    """The OpenActive side of the selected pair, and how the batch matched it."""
    with st.expander(f"Match · {row.site_name}", expanded=True):
        st.caption(
            f"{row.local_authority_name} · {row.postcode or coverage.UNKNOWN} · "
            f"{row.ownership_type_group}"
        )
        for label, value in coverage.pair_facts(row):
            st.markdown(f"**{label}** — {value}")
        for url in row.dataset_urls:
            st.markdown(f"- [{url}]({url})")


def render_table_section(monitor: Monitor, rows: Sequence[MappingRow]) -> None:
    """Filters, the table of pairs and the selected pair."""
    state = render_filters(monitor, rows)
    shown = coverage.sort_rows(
        monitor,
        coverage.apply_filters(
            rows,
            search=state.search,
            selections=state.selections,
            primary_only=state.past_threshold_only,
        ),
    )

    st.caption(
        f"{len(shown):,} of {len(rows):,} site-venue pairs shown, most opportunities first. "
        "A site matched to several venues carries one row per pair. Select a row to read the "
        "match."
    )
    selected = render_table(
        coverage.to_dataframe(monitor, shown),
        coverage.tone_frame(monitor, shown),
        columns=monitor.columns,
        rag_columns=transforms.rag_columns(monitor),
        key=f"table_{monitor.id}",
        empty_message=(
            "No pairs match these filters in this snapshot. Clear the filters to see every "
            "match the run made."
        ),
    )
    if selected is not None and selected < len(shown):
        render_row_detail(shown[selected])


def render_coverage_page(monitor: Monitor) -> None:
    """Header, blurb, figures, the regional chart, filters, table, selected pair, footer."""
    try:
        response = repository.fetch_coverage(monitor.id)
    except ApiError as exc:
        layout.render_error_header(monitor.crumb, monitor.name)
        render_api_error(exc)
        return

    layout.render_header(monitor.crumb, monitor.name, response.meta)
    render_blurb(monitor)
    render_stats(monitor, response.data)
    render_summary_chart(monitor, response.data)

    try:
        mappings = repository.fetch_site_mappings(monitor.id, monitor.rows_id)
    except ApiError as exc:
        # The rows are a second endpoint. One that is not live costs the page its table, the
        # way a missing trend costs a monitor page its chart — never the figures above it.
        render_api_error(exc)
        layout.render_footer(monitor.query, actions=NO_ACTIONS)
        return

    render_table_section(monitor, coverage.build_rows(mappings.data))
    layout.render_footer(monitor.query, actions=NO_ACTIONS)
