"""The landing page: fleet KPIs, the contact-queue banner, and one tile per monitor."""

from __future__ import annotations

import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import Summary
from stewards.components import layout, nav, theme
from stewards.components.errors import render_api_error
from stewards.components.surface import card
from stewards.config import get_settings
from stewards.monitors.overview import Tile, build_tiles, format_delta
from stewards.monitors.thresholds import Tone
from stewards.monitors.trend import sparkline_chart

TILES_PER_ROW = 3


def render_fleet_kpis(summary: Summary) -> None:
    issue_share = (
        f"{summary.publishers_with_issues / summary.publishers_monitored:.1%} of the fleet"
        if summary.publishers_monitored
        else "fleet size unknown"
    )
    cells = (
        (
            "Publishers monitored",
            f"{summary.publishers_monitored:,}",
            None,
            f"{summary.feeds:,} feeds across {summary.datasets:,} datasets",
            None,
        ),
        (
            "Publishers with issues",
            f"{summary.publishers_with_issues:,}",
            format_delta(summary.publishers_with_issues_delta),
            issue_share,
            Tone.AMBER,
        ),
        (
            "Open incidents",
            f"{summary.open_incidents:,}",
            format_delta(summary.open_incidents_delta),
            f"across {len(summary.monitors)} monitors",
            Tone.RED,
        ),
        (
            "Past contact threshold",
            f"{summary.past_threshold:,}",
            format_delta(summary.past_threshold_delta),
            f"open longer than {get_settings().contact_threshold_days} days",
            Tone.RED,
        ),
    )
    for index, (column, (label, value, delta, sub, tone)) in enumerate(
        zip(st.columns(4), cells, strict=True)
    ):
        with column, card(f"kpi_{index}"):
            layout.tone_metric(
                label,
                value,
                tone if value != "0" else Tone.GREEN,
                slug=f"fleet{index}",
                delta=delta,
                sub=sub,
            )


def render_threshold_banner(summary: Summary, threshold_days: int) -> None:
    if summary.past_threshold <= 0:
        st.success(
            f"No incident has been open longer than {threshold_days} days in this snapshot.",
            icon=":material/check_circle:",
        )
        return
    banner, action = st.columns([4, 1], vertical_alignment="center")
    with banner:
        st.warning(
            f"**{summary.past_threshold} incidents have passed the {threshold_days}-day "
            "contact threshold.** Incidents open longer than the threshold move into the "
            "publisher contact queue, ordered by days open.",
            icon=":material/schedule:",
        )
    with action:
        if st.button("Review contact queue", width="stretch", type="primary"):
            nav.switch_to("contact_queue")


def render_tile(tile: Tile) -> None:
    """Name and state chip, then the count beside its sparkline, then the note and a link."""
    colour = theme.markdown_colour(tile.state)
    with card(f"tile_{tile.monitor.id}"):
        with st.container(
            horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
        ):
            st.markdown(f"**{tile.monitor.name}**")
            st.badge(tile.state_label.upper(), color=colour)
        st.caption(tile.monitor.group.value)

        count_col, spark_col = st.columns([1, 1], vertical_alignment="center")
        with count_col:
            layout.tone_metric(
                "", tile.value, tile.state, slug=f"tile{tile.monitor.id}", sub=tile.monitor.unit
            )
        with spark_col:
            chart = sparkline_chart(tile.sparkline, theme.FOREGROUND[tile.state])
            if chart is not None:
                st.altair_chart(chart, width="stretch")

        st.divider()
        with st.container(
            horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
        ):
            st.markdown(f":{colour}[●] :gray[{tile.note}]")
            page = nav.page_for(tile.monitor.id)
            if page is not None:
                st.page_link(page, label="Open")


def render_overview_page() -> None:
    try:
        response = repository.fetch_summary()
    except ApiError as exc:
        layout.render_error_header("Overview", "Health of the publisher fleet")
        render_api_error(exc)
        return

    summary = response.data
    tiles = build_tiles(summary)
    title = (
        f"Health of {summary.publishers_monitored:,} publishers"
        if summary.publishers_monitored
        else "Health of the publisher fleet"
    )
    layout.render_header(
        "Overview",
        title,
        response.meta,
    )
    if get_settings().use_sample_data:
        layout.render_sample_data_notice()

    render_fleet_kpis(summary)
    threshold_days = get_settings().contact_threshold_days
    render_threshold_banner(summary, threshold_days)

    with st.container(horizontal=True, vertical_alignment="bottom"):
        st.markdown("**Monitors**")
        st.caption(
            f"{len(tiles)} registered · monitors register themselves here as their API "
            "endpoints go live"
        )
    for start in range(0, len(tiles), TILES_PER_ROW):
        row = tiles[start : start + TILES_PER_ROW]
        for column, tile in zip(st.columns(TILES_PER_ROW), row, strict=False):
            with column:
                render_tile(tile)

    layout.render_footer("view_monitor_overview")
