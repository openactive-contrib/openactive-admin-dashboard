"""The whole of an activity coverage monitor's page.

Header, blurb, the three national figures, the national ranking, then the district map with
its filters and the table beneath it. `coverage_page.py` is the nearest neighbour; this page
reads three resources instead of two and loads its rows with the page, because the map is
what a steward comes here for.

The activity filter and the map's own "centre on" picker are the page's two filters. Both
live in the map's fragment, so changing either reruns the map and the table only, and the
table always describes exactly what the map shows.

No email draft: an untagged opportunity is not a fault a publisher is contacted about. A
thin renderer, as the hard rules require: every figure, label and column comes from
`monitors.activity_coverage` and the registry entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from stewards import geography
from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import ActivityCoverageSummary, TopActivity
from stewards.components import layout, map_chart, theme
from stewards.components.errors import render_api_error
from stewards.components.incident_table import render_table
from stewards.components.loading import loading
from stewards.components.surface import card
from stewards.monitors import activity_coverage, transforms
from stewards.monitors.activity_coverage import ActivityRow
from stewards.monitors.registry import Monitor

#: An untagged opportunity is not a fault a publisher is contacted about, so this page drafts
#: no email and must not say it does.
NO_ACTIONS = "none"

#: The first option of every filter: no narrowing.
ALL_ACTIVITIES = "All activities"


def render_blurb(monitor: Monitor) -> None:
    with card(f"blurb_{monitor.id}"):
        st.markdown(monitor.blurb)
        st.caption(" · ".join(f"`{chip}`" for chip in monitor.meta_chips))


def render_stats(monitor: Monitor, summary: ActivityCoverageSummary) -> None:
    """The national figures: they describe the whole snapshot, and no filter moves them."""
    stats = activity_coverage.page_stats(summary)
    for index, (column, stat) in enumerate(zip(st.columns(len(stats)), stats, strict=True)):
        with column, card(f"kpi_{monitor.id}_{index}"):
            layout.tone_metric(
                stat.label, stat.value, stat.tone, slug=f"{monitor.id}{index}", sub=stat.sub
            )


def render_top_activities(monitor: Monitor, activities: Sequence[TopActivity]) -> None:
    chart = activity_coverage.top_activity_chart(
        activities,
        theme.TEAL,
        label_colour=theme.MUTED,
        grid_colour=theme.BORDER_SUBTLE,
    )
    with card(f"chart_{monitor.id}_top"):
        st.subheader(
            f"Top {repository.TOP_ACTIVITY_LIMIT} activities", anchor=False, divider=False
        )
        if chart is None:
            st.caption("This snapshot does not report a national ranking.")
            return
        st.altair_chart(chart, width="stretch")
        st.caption(
            "Opportunities tagged with each activity across the UK. An opportunity tagged "
            "with two activities counts under both."
        )


def filter_key(monitor: Monitor, field: str) -> str:
    return f"filter_{monitor.id}_{field}"


def selections(monitor: Monitor) -> dict[str, str]:
    """Each declared filter's current choice, empty for "all"."""
    chosen = {
        spec.field: st.session_state.get(filter_key(monitor, spec.field), ALL_ACTIVITIES)
        for spec in monitor.filters
    }
    return {
        field: "" if value == ALL_ACTIVITIES else str(value) for field, value in chosen.items()
    }


def render_filters(monitor: Monitor, rows: Sequence[ActivityRow]) -> None:
    """One selectbox per declared filter, drawn inside the map card."""
    # At least two columns, so a lone filter takes half the card rather than all of it.
    for column, spec in zip(
        st.columns(max(2, len(monitor.filters))), monitor.filters, strict=False
    ):
        column.selectbox(
            spec.label,
            [ALL_ACTIVITIES, *activity_coverage.filter_options(rows, spec.field)],
            key=filter_key(monitor, spec.field),
        )


def render_table_section(
    monitor: Monitor, rows: Sequence[ActivityRow], district_code: str | None
) -> None:
    """The rows behind the map, narrowed by the same two filters and a search on the local
    authority's name, which narrows the table only."""
    district = activity_coverage.district_label(rows, district_code)
    with card(f"table_card_{monitor.id}"):
        st.subheader("Activities by local authority", anchor=False, divider=False)
        search = st.text_input(
            "Search local authorities",
            key=f"search_{monitor.id}",
            placeholder="Search by local authority name…",
            label_visibility="collapsed",
        )
        shown = activity_coverage.sort_rows(
            monitor,
            activity_coverage.apply_filters(
                rows,
                search=search,
                selections=selections(monitor),
                district_code=district_code,
            ),
        )
        st.caption(activity_coverage.table_caption(len(shown), len(rows), district=district))
        render_table(
            activity_coverage.to_dataframe(monitor, shown),
            activity_coverage.tone_frame(monitor, shown),
            columns=monitor.columns,
            rag_columns=transforms.rag_columns(monitor),
            key=f"table_{monitor.id}",
            empty_message=(
                "No activity is reported for these filters in this snapshot. Clear the search, "
                "choose another activity, or show the whole map."
            ),
            selectable=False,
        )


def render_map_section(monitor: Monitor, rows: Sequence[ActivityRow]) -> None:
    """The district map with the activity filter above it and the table below it."""
    boundaries = geography.uk_local_authority_districts()
    key = f"chart_{monitor.id}_districts"

    def activity() -> str | None:
        return selections(monitor).get("activity") or None

    map_chart.render_map(
        key,
        "Where activities are offered",
        lambda focus: activity_coverage.district_map(
            rows,
            boundaries,
            map_chart.map_style(activity_coverage.legend_title(activity())),
            activity=activity(),
            focus=focus,
        ),
        areas=boundaries,
        height=map_chart.map_style("").height,
        focus_placeholder="Filter to a local authority…",
        caption=lambda: activity_coverage.map_note(rows, boundaries, activity()),
        attribution=boundaries.attribution,
        empty_message="No local authority reports this activity in this snapshot.",
        controls=lambda: render_filters(monitor, rows),
        after=lambda focus: render_table_section(monitor, rows, focus),
    )


def render_activity_coverage_page(monitor: Monitor) -> None:
    """Header, blurb, figures, the ranking, the map and its table, footer.

    The summary is the page: if it fails there is no snapshot to name. The ranking and the
    rows are further resources, and either failing costs only its own section.
    """
    try:
        with loading("Loading the activity coverage snapshot"):
            response = repository.fetch_activity_summary(monitor.id)
    except ApiError as exc:
        layout.render_error_header(monitor.crumb, monitor.name)
        render_api_error(exc)
        return

    layout.render_header(monitor.crumb, monitor.name, response.meta)
    render_blurb(monitor)
    render_stats(monitor, response.data)

    try:
        with loading("Loading the national ranking"):
            top = repository.fetch_top_activities(monitor.id)
    except ApiError as exc:
        render_api_error(exc)
    else:
        render_top_activities(monitor, top.data)

    try:
        with loading("Loading activities by local authority"):
            districts = repository.fetch_activity_districts(monitor.id)
    except ApiError as exc:
        render_api_error(exc)
    else:
        render_map_section(monitor, activity_coverage.build_rows(districts.data))

    layout.render_footer(monitor.query, actions=NO_ACTIONS)
