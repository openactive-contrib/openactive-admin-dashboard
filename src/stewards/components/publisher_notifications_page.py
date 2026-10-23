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

#: Widget key of the table, so a save can clear the selection: the row may move.
TABLE_KEY = "table_publisher_notifications"

#: Survives the rerun after a save, so the confirmation is shown on the fresh page.
FLASH_KEY = "pn_flash"


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
    flash = st.session_state.pop(FLASH_KEY, None)
    if flash:
        st.success(flash, icon=":material/check_circle:")
    _render_blurb()
    _render_kpis(summary.data)
    rows = _render_filters(list(listing.data))
    selected = _render_table(rows, today)
    if selected is not None:
        _render_detail(selected, today)
    layout.render_footer(
        QUERY,
        note="These rows are stored; the daily batch does not rewrite them.",
        actions="draft publisher email, update row, delete row",
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
        key=TABLE_KEY,
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
        render_edit_form(row, today)
        render_editable_draft(draft_publisher_email(row, today), key=draft_key(row))
        if st.button("Delete row", icon=":material/delete:", key=f"pn_delete_{row.id}"):
            _confirm_delete(row)


def render_edit_form(row: PublisherNotification, today: date) -> None:
    """Edit the five steward fields and save the whole row back."""
    # Keyed by row and last save: a new row, or a saved one, starts from its stored values.
    prefix = f"pn_edit_{draft_key(row)}"
    with st.form(prefix, border=False):
        st.markdown("**Update this row**")
        status_col, stakeholder_col, contact_col, date_col = st.columns(4)
        status = status_col.selectbox(
            "Status",
            pn.STATUSES,
            index=pn.STATUSES.index(row.status) if row.status in pn.STATUSES else None,
            format_func=status_label,
            placeholder="Choose a status",
            key=f"{prefix}_status",
        )
        stakeholder = stakeholder_col.text_input(
            "Stakeholder", value=row.stakeholder or "", key=f"{prefix}_stakeholder"
        )
        contact = contact_col.text_input(
            "Contact", value=row.contact or "", key=f"{prefix}_contact"
        )
        picked = date_col.date_input(
            "Date contacted",
            value=row.date_contacted,
            max_value=today,
            format="YYYY-MM-DD",
            key=f"{prefix}_date",
        )
        notes = st.text_area(
            "Notes",
            value=row.notes or "",
            max_chars=pn.NOTES_MAX,
            key=f"{prefix}_notes",
        )
        submitted = st.form_submit_button("Save", type="primary")

    if not submitted:
        return

    # `date_input` can in principle return a range; this one is a single date or empty.
    contacted = picked if isinstance(picked, date) else None
    body, errors = pn.apply_edits(
        row,
        status=status or "",
        stakeholder=stakeholder,
        contact=contact,
        date_contacted=contacted,
        notes=notes,
        today=today,
    )
    if body is None:
        for error in errors:
            st.error(error)
        return

    try:
        repository.update_publisher_notification(body)
    except ApiError as exc:
        render_api_error(exc)
        return

    st.session_state[FLASH_KEY] = f"Saved {row.publisher_name or row.id}."
    st.session_state.pop(TABLE_KEY, None)
    st.rerun()


def delete_row(row: PublisherNotification) -> bool:
    """Delete the row, then reload the page. False, with the error shown, if it failed."""
    try:
        repository.delete_publisher_notification(row.id)
    except ApiError as exc:
        render_api_error(exc)
        return False

    st.session_state[FLASH_KEY] = f"Deleted {row.publisher_name or row.id}."
    st.session_state.pop(TABLE_KEY, None)
    st.rerun()
    return True


@st.dialog("Delete this row?")
def _confirm_delete(row: PublisherNotification) -> None:
    """Ask before deleting. Nothing is removed until the steward confirms."""
    st.markdown(
        f"This permanently removes **{row.publisher_name or row.id}** from the notification "
        "list, including its problems and notes. It cannot be undone."
    )
    st.caption(f"Row id: `{row.id}`")
    cancel_col, delete_col = st.columns(2)
    if cancel_col.button("Cancel", key="pn_delete_cancel", width="stretch"):
        st.rerun()
    if delete_col.button("Delete", type="primary", key="pn_delete_confirm", width="stretch"):
        delete_row(row)
