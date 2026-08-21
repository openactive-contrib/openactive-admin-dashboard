"""The landing page: fleet KPIs, the contact-queue banner, and one tile per monitor."""

from __future__ import annotations

import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import Summary
from stewards.components import layout, nav
from stewards.components.errors import render_api_error
from stewards.config import get_settings
from stewards.monitors.overview import Tile, build_tiles
from stewards.monitors.thresholds import Tone

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
            f"{summary.feeds:,} feeds across {summary.datasets:,} datasets",
            Tone.GREY,
        ),
        (
            "Publishers with issues",
            f"{summary.publishers_with_issues:,}",
            issue_share,
            Tone.AMBER,
        ),
        (
            "Open incidents",
            f"{summary.open_incidents:,}",
            f"across {len(summary.monitors)} monitors",
            Tone.RED,
        ),
        (
            "Past contact threshold",
            f"{summary.past_threshold:,}",
            "in the publisher contact queue",
            Tone.RED,
        ),
    )
    for column, (label, value, sub, tone) in zip(st.columns(4), cells, strict=True):
        with column, st.container(border=True):
            layout.tone_metric(label, value, tone if value != "0" else Tone.GREEN)
            st.caption(sub)


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
        if st.button("Review contact queue", use_container_width=True, type="primary"):
            nav.switch_to("contact_queue")


def render_tile(tile: Tile) -> None:
    with st.container(border=True):
        st.markdown(f"**{tile.monitor.name}**")
        st.caption(tile.monitor.group.value)
        layout.tone_metric(tile.state_label, tile.value, tile.state)
        st.caption(tile.monitor.unit)
        if tile.sparkline:
            st.line_chart(list(tile.sparkline), height=70, use_container_width=True)
        st.caption(tile.note)
        if st.button("Open", key=f"open_{tile.monitor.id}", use_container_width=True):
            nav.switch_to(tile.monitor.id)


def render_overview_page() -> None:
    try:
        response = repository.fetch_summary()
    except ApiError as exc:
        layout.render_error_header("Overview", "Health of the publisher fleet")
        render_api_error(exc)
        return

    summary = response.data
    layout.render_header("Overview", "Health of the publisher fleet", response.meta)
    if get_settings().use_sample_data:
        layout.render_sample_data_notice()

    render_fleet_kpis(summary)
    threshold_days = get_settings().contact_threshold_days
    render_threshold_banner(summary, threshold_days)

    tiles = build_tiles(summary)
    st.subheader("Monitors", anchor=False)
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
