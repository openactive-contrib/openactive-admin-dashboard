"""The whole of a monitor page. Every page stub is three lines because of this module."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import Incident, OrphanedChildrenDetail
from stewards.components import layout
from stewards.components.email_draft import render_email_draft
from stewards.components.errors import render_api_error
from stewards.components.filters import render_filters
from stewards.components.incident_table import render_monitor_table
from stewards.components.surface import card
from stewards.components.trend_chart import render_figure
from stewards.monitors import transforms
from stewards.monitors.registry import Monitor


def render_blurb(monitor: Monitor) -> None:
    with card(f"blurb_{monitor.id}"):
        st.markdown(monitor.blurb)
        st.caption(" · ".join(f"`{chip}`" for chip in monitor.meta_chips))


def render_kpis(monitor: Monitor, rows: Sequence[transforms.Row]) -> None:
    kpis = transforms.monitor_kpis(monitor, rows)
    for index, (column, kpi) in enumerate(zip(st.columns(3), kpis, strict=True)):
        with column, card(f"kpi_{monitor.id}_{index}"):
            layout.tone_metric(kpi.label, kpi.value, kpi.tone, slug=f"{monitor.id}{index}")


def render_missing_parents(monitor: Monitor, incident: Incident) -> None:
    """The parent ids a selected dataset's children point at that its feed does not contain.

    Only rendered where the monitor's detail model carries them, so it stays a property of
    the payload rather than a branch on the monitor id.
    """
    detail = transforms.parse_detail(monitor, incident)
    if not isinstance(detail, OrphanedChildrenDetail) or not detail.missing_parents:
        return
    with st.expander(f"Missing parents · {incident.publisher_name or incident.publisher_id}"):
        st.caption(
            "The parent ids these children reference that the dataset's parent feed does "
            "not contain, worst first. The API reports the largest offenders, not all of "
            f"the {detail.missing_parent_count or 0:,} it counted."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Missing parent id": parent.missing_id or transforms.EMPTY,
                        "Children affected": parent.child_count,
                    }
                    for parent in detail.missing_parents
                ]
            ),
            column_config={
                "Missing parent id": st.column_config.TextColumn(width="large"),
                "Children affected": st.column_config.NumberColumn(format="%d"),
            },
            hide_index=True,
            width="stretch",
            key=f"parents_{monitor.id}_{incident.publisher_id}",
        )


def render_monitor_page(monitor: Monitor) -> None:
    """Header, blurb, KPIs, trend, filters, table, row detail, footer."""
    try:
        page = repository.fetch_incidents(monitor.id)
    except ApiError as exc:
        layout.render_error_header(monitor.crumb, monitor.name)
        render_api_error(exc)
        return

    # The trend is read separately and tolerantly: a monitor whose trend endpoint this
    # deployment has not built yet loses its chart, not its incidents.
    trend = repository.fetch_trend_points(monitor.id)
    incidents = list(page.data)

    # Everything above the filters is rendered into reserved slots, because the KPIs need
    # the *filtered* frame, which only exists once the filter widgets have been read. The
    # slots keep the on-screen order the brief specifies: header, blurb, KPIs, trend,
    # filters, table.
    header_slot = st.container()
    blurb_slot = st.container()
    kpi_slot = st.container()
    trend_slot = st.container()
    filter_slot = st.container()

    # One row per incident, or one per breakdown item where the monitor declares one. The
    # filters read rows too, so a breakdown field can be filtered on like any other.
    rows = transforms.expand(monitor, incidents)

    with filter_slot:
        state = render_filters(monitor, rows)

    shown = transforms.sort_rows(
        monitor,
        transforms.apply_filters(
            monitor,
            rows,
            search=state.search,
            selections=state.selections,
            past_threshold_only=state.past_threshold_only,
        ),
    )
    frame = transforms.to_dataframe(monitor, shown)
    tones = transforms.tone_frame(monitor, shown)

    with header_slot:
        layout.render_header(monitor.crumb, monitor.name, page.meta)
    with blurb_slot:
        render_blurb(monitor)
    with kpi_slot:
        render_kpis(monitor, shown)
    with trend_slot, card(f"trend_{monitor.id}"):
        # The unfiltered rows: this is the snapshot's own figure, the one the overview card
        # shows, and a filter narrowing the table must not appear to move it.
        render_figure(monitor, trend, transforms.snapshot_total(monitor, rows))

    st.caption(
        f"{len(shown):,} of {len(rows):,} rows shown across "
        f"{len(transforms.unique_incidents(shown)):,} of {len(incidents):,} incidents. "
        "Select a row to draft a publisher email."
    )
    selected = render_monitor_table(
        monitor, frame, tones, rag_columns=transforms.rag_columns(monitor)
    )
    if selected is not None and selected < len(shown):
        incident = shown[selected].incident
        render_email_draft(monitor, incident, page.meta.snapshot_date)
        render_missing_parents(monitor, incident)

    layout.render_footer(monitor.query)
