"""The whole of a monitor page. Every page stub is three lines because of this module."""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import Incident
from stewards.components import layout
from stewards.components.email_draft import render_email_draft
from stewards.components.errors import render_api_error
from stewards.components.filters import render_filters
from stewards.components.incident_table import render_monitor_table
from stewards.components.trend_chart import render_trend
from stewards.config import get_settings
from stewards.monitors import transforms
from stewards.monitors.registry import Monitor


def render_blurb(monitor: Monitor) -> None:
    with st.container(border=True):
        st.markdown(monitor.blurb)
        st.caption(" · ".join(f"`{chip}`" for chip in monitor.meta_chips))


def render_kpis(monitor: Monitor, incidents: Sequence[Incident]) -> None:
    kpis = transforms.monitor_kpis(monitor, incidents)
    for column, kpi in zip(st.columns(3), kpis, strict=True):
        with column:
            layout.tone_metric(kpi.label, kpi.value, kpi.tone)


def render_monitor_page(monitor: Monitor) -> None:
    """Header, blurb, KPIs, trend, filters, table, row detail, footer."""
    try:
        page = repository.fetch_incidents(monitor.id)
        trend = repository.fetch_trend(monitor.id)
    except ApiError as exc:
        layout.render_error_header(monitor.crumb, monitor.name)
        render_api_error(exc)
        return

    incidents = list(page.data)
    state = render_filters(monitor, incidents)
    shown = transforms.sort_by_age(
        transforms.apply_filters(
            monitor,
            incidents,
            search=state.search,
            selections=state.selections,
            past_threshold_only=state.past_threshold_only,
        )
    )
    frame = transforms.to_dataframe(monitor, shown)
    tones = transforms.tone_frame(monitor, shown)

    layout.render_header(
        monitor.crumb, monitor.name, page.meta, export=frame, export_name=monitor.id
    )
    if get_settings().use_sample_data:
        layout.render_sample_data_notice()
    render_blurb(monitor)
    render_kpis(monitor, shown)
    render_trend(monitor, trend.data)

    st.caption(
        f"{len(shown):,} of {len(incidents):,} incidents shown. "
        "Select a row to draft a publisher email."
    )
    selected = render_monitor_table(
        monitor, frame, tones, rag_columns=transforms.rag_columns(monitor)
    )
    if selected is not None and selected < len(shown):
        render_email_draft(monitor, shown[selected], page.meta.snapshot_date)

    layout.render_footer(monitor.query)
