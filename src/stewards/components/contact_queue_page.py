"""The cross-monitor contact queue page."""

from __future__ import annotations

import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.components import layout
from stewards.components.email_draft import render_email_draft
from stewards.components.errors import render_api_error
from stewards.components.incident_table import render_table
from stewards.components.surface import card
from stewards.config import get_settings
from stewards.monitors import contact_queue
from stewards.monitors.registry import get_monitor
from stewards.monitors.transforms import sort_by_age

TITLE = "Contact queue"
QUERY = "view_contact_queue"


def render_contact_queue_page() -> None:
    threshold_days = get_settings().contact_threshold_days
    try:
        page = repository.fetch_contact_queue()
    except ApiError as exc:
        layout.render_error_header("Cross-monitor", TITLE)
        render_api_error(exc)
        return

    incidents = list(page.data)
    rows = sort_by_age(contact_queue.known_incidents(incidents))
    frame = contact_queue.to_dataframe(incidents)
    tones = contact_queue.tone_frame(incidents)

    layout.render_header(
        "Cross-monitor", TITLE, page.meta, export=frame, export_name="contact_queue"
    )
    if get_settings().use_sample_data:
        layout.render_sample_data_notice()

    with card("blurb_contact_queue"):
        st.markdown(
            f"Every incident open longer than the {threshold_days}-day threshold, across all "
            "monitors, oldest first. This is the list a steward works through when writing to "
            "publishers."
        )
        st.caption(
            " · ".join(
                f"`{chip}`"
                for chip in (
                    "view.contact_queue",
                    f"threshold: {threshold_days} days",
                    "all monitors",
                )
            )
        )

    for index, (column, (label, value, tone)) in enumerate(
        zip(st.columns(3), contact_queue.queue_kpis(incidents), strict=True)
    ):
        with column, card(f"kpi_queue_{index}"):
            layout.tone_metric(label, value, tone)

    skipped = contact_queue.unknown_monitor_ids(incidents)
    if skipped:
        st.info(
            "Rows from monitors this build does not yet render are hidden: "
            + ", ".join(f"`{m}`" for m in skipped),
            icon=":material/info:",
        )

    st.caption(f"{len(rows):,} incidents. Select a row to draft a publisher email.")
    selected = render_table(
        frame,
        tones,
        columns=contact_queue.COLUMN_SPECS,
        rag_columns=contact_queue.RAG_COLUMNS,
        key="table_contact_queue",
        empty_message=(
            f"Nothing has been open for {threshold_days} days or more in this snapshot."
        ),
    )
    if selected is not None and selected < len(rows):
        incident = rows[selected]
        render_email_draft(get_monitor(incident.monitor_id), incident, page.meta.snapshot_date)

    layout.render_footer(QUERY)
