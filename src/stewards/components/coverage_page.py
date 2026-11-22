"""The whole of a coverage monitor's page.

`quality_page.py` is this module's nearest neighbour: same shape, read differently. A
coverage run reports how much of an external estate the fleet reaches — four figures from one
endpoint, the site-venue pairs behind them from a second — so the page is header, blurb, four
figures, the regional chart where a trend would be, the local authority map, then the pairs:
filters, table and the selected pair's OpenActive side. Everything it shows was computed in
`monitors.coverage`.

The pairs load on request. They are ten thousand rows over several pages, and most visits
are for the figures and the map, so the page does not make every visitor wait for them.

No email draft: a coverage gap is not a fault a publisher is contacted about, which is also
why the monitor is informational and carries no sidebar pill.

A thin renderer, as the hard rules require: no arithmetic here, and every column, filter and
label comes off the registry entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from stewards import geography
from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import CoverageSnapshot
from stewards.components import layout, map_chart, theme
from stewards.components.errors import render_api_error
from stewards.components.filters import ALL, FilterState
from stewards.components.incident_table import render_table
from stewards.components.loading import loading
from stewards.components.row_dialog import render_row_dialog
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


def render_la_map(monitor: Monitor, snapshot: CoverageSnapshot) -> None:
    """Coverage by local authority: the district-level picture the regional bars average."""
    boundaries = geography.uk_local_authority_districts()
    style = map_chart.map_style("Coverage %")
    map_chart.render_map(
        f"chart_{monitor.id}_la",
        "Coverage by local authority",
        lambda focus: coverage.local_authority_map(snapshot, boundaries, style, focus),
        areas=coverage.map_areas(boundaries),
        height=style.height,
        focus_placeholder="Centre on a local authority…",
        caption=coverage.map_note(snapshot, boundaries),
        attribution=boundaries.attribution,
        empty_message="This snapshot does not report a local authority breakdown.",
    )


def pairs_loaded_key(monitor: Monitor) -> str:
    """Session flag set once the steward asks for the pairs, so filter reruns keep them."""
    return f"pairs_loaded_{monitor.id}"


def _request_pairs(key: str) -> None:
    st.session_state[key] = True


def render_mappings_section(monitor: Monitor) -> None:
    """The pairs, fetched only once asked for.

    The button sets the flag in its callback, which runs before the script does, so the run
    the click starts already renders the table.
    """
    key = pairs_loaded_key(monitor)
    if not st.session_state.get(key, False):
        with card(f"pairs_{monitor.id}"):
            st.subheader("Site-venue pairs", anchor=False, divider=False)
            st.caption(coverage.PAIRS_PROMPT)
            st.button(
                "Load site-venue pairs",
                key=f"load_pairs_{monitor.id}",
                type="primary",
                on_click=_request_pairs,
                args=(key,),
            )
        return

    try:
        with loading("Loading site-venue pairs"):
            mappings = repository.fetch_site_mappings(monitor.id, monitor.rows_id)
    except ApiError as exc:
        # The rows are a second endpoint. One that is not live costs the page its table, the
        # way a missing trend costs a monitor page its chart — never the figures above it.
        render_api_error(exc)
        return

    render_table_section(monitor, coverage.build_rows(mappings.data))


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
    """The OpenActive side of the selected pair, and how the batch matched it. Shown in the
    row pop-up."""
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
        "A site matched to several venues carries one row per pair. Select a row to open it."
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
    render_row_dialog(
        shown,
        lambda row: f"{row.site_id}:{row.venue_name}:{row.dataset_url}",
        selected,
        key=f"dialog_{monitor.id}",
        table_key=f"table_{monitor.id}",
        title=lambda row: f"Match · {row.site_name}",
        render=render_row_detail,
    )


def render_coverage_page(monitor: Monitor) -> None:
    """Header, blurb, figures, the regional chart, the map, the pairs on request, footer."""
    try:
        with loading("Loading the coverage snapshot"):
            response = repository.fetch_coverage(monitor.id)
    except ApiError as exc:
        layout.render_error_header(monitor.crumb, monitor.name)
        render_api_error(exc)
        return

    layout.render_header(monitor.crumb, monitor.name, response.meta)
    render_blurb(monitor)
    render_stats(monitor, response.data)
    render_summary_chart(monitor, response.data)
    render_la_map(monitor, response.data)
    render_mappings_section(monitor)
    layout.render_footer(monitor.query, actions=NO_ACTIONS)
