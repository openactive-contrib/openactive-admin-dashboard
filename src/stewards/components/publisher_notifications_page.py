"""The publisher notification list: stored steward work rows, one per publisher."""

from __future__ import annotations

from datetime import date

import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import PublisherNotification, PublisherNotificationSummary
from stewards.components import layout
from stewards.components.email_draft import draft_key, render_editable_draft
from stewards.components.errors import render_api_error
from stewards.components.incident_table import render_table
from stewards.components.loading import loading
from stewards.components.surface import card
from stewards.monitors import publisher_notifications as pn
from stewards.monitors.email_draft import draft_publisher_email
from stewards.monitors.thresholds import status_label

CRUMB = "Publishers"
TITLE = "Publisher notification list"
QUERY = "publisher_notifications"
SOURCE = "Stored work list · updated as you save"

#: The "no filter" choice in a dropdown. `apply_filters` reads an empty string as "any".
ANY = ""


def render_publisher_notifications_page() -> None:
    try:
        with loading("Loading the notification list"):
            listing = repository.fetch_publisher_notifications()
            summary = repository.fetch_publisher_notification_summary()
    except ApiError as exc:
        layout.render_error_header(CRUMB, TITLE)
        render_api_error(exc)
        return

    today = date.today()
    layout.render_header(CRUMB, TITLE, summary.meta, source=SOURCE)
    _render_blurb()
    _render_kpis(summary.data)
    rows = _render_filters(list(listing.data))
    selected = _render_table(rows, today)
    if selected is not None:
        _render_detail(selected, today)
    layout.render_footer(
        QUERY, note="These rows are stored; the daily batch does not rewrite them."
    )


def _render_blurb() -> None:
    with card("blurb_publisher_notifications"):
        st.markdown(
            "One row per publisher with open problems, kept by the stewards team. Rows that "
            "need attention today come first: never contacted, no follow-up for more than "
            f"{pn.FOLLOW_UP_DAYS} days, or a problem past its Slack alert threshold."
        )


def _render_kpis(summary: PublisherNotificationSummary) -> None:
    stats = pn.summary_stats(summary)
    for index, (column, (label, value, tone)) in enumerate(
        zip(st.columns(len(stats)), stats, strict=True)
    ):
        with column, card(f"kpi_notifications_{index}"):
            layout.tone_metric(label, value, tone, slug=f"notifications{index}")


def _render_filters(rows: list[PublisherNotification]) -> list[PublisherNotification]:
    statuses, stakeholders = pn.filter_options(rows)
    search_col, status_col, stakeholder_col, closed_col = st.columns(
        [3, 2, 2, 1.4], vertical_alignment="bottom"
    )
    search = search_col.text_input(
        "Search", placeholder="Publisher, id or contact", key="pn_search"
    )
    status = status_col.selectbox(
        "Status",
        [ANY, *statuses],
        format_func=lambda value: status_label(value) if value else "Any",
        key="pn_status",
    )
    stakeholder = stakeholder_col.selectbox(
        "Stakeholder",
        [ANY, *stakeholders],
        format_func=lambda value: value or "Any",
        key="pn_stakeholder",
    )
    show_closed = closed_col.toggle("Show closed", key="pn_show_closed")
    return pn.apply_filters(
        rows,
        search=search,
        status=status or ANY,
        stakeholder=stakeholder or ANY,
        # Choosing "Closed" in the dropdown is asking to see closed rows.
        show_closed=show_closed or status == "closed",
    )


def _render_table(
    rows: list[PublisherNotification], today: date
) -> PublisherNotification | None:
    ordered = pn.sort_rows(rows, today)
    st.caption(
        f"{len(ordered):,} publishers. Select a row to see its problems and draft an email."
    )
    position = render_table(
        pn.to_dataframe(ordered, today),
        pn.tone_frame(ordered),
        columns=pn.COLUMN_SPECS,
        rag_columns=pn.RAG_COLUMNS,
        key="table_publisher_notifications",
        empty_message="No publishers match these filters.",
    )
    if position is None or position >= len(ordered):
        return None
    return ordered[position]


def _render_detail(row: PublisherNotification, today: date) -> None:
    with card("notification_detail"):
        st.markdown(f"**{row.publisher_name or row.publisher_id}**")
        reasons = pn.alert_reasons(row, today)
        if reasons:
            st.warning(" · ".join(reasons), icon=":material/notifications:")
        st.caption(
            " · ".join(
                [
                    f"Status: {status_label(row.status) if row.status else '—'}",
                    f"Stakeholder: {row.stakeholder or '—'}",
                    f"Contact: {row.contact or '—'}",
                    f"Last contacted: {row.date_contacted or '—'}",
                ]
            )
        )
        if row.notes:
            st.text(row.notes)

        problems = pn.problem_frame(row)
        if problems.empty:
            st.info("No problems are recorded on this row.")
        else:
            st.dataframe(problems, hide_index=True, width="stretch")

        render_editable_draft(draft_publisher_email(row, today), key=draft_key(row))
