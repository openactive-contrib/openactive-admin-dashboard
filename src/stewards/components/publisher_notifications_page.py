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
from stewards.components.row_dialog import render_row_dialog
from stewards.components.surface import DANGER_PREFIX, card
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

#: The "Saved" message, shown inside the pop-up that stays open after a save.
SAVED_KEY = "pn_saved"

#: Save is the main action in the pop-up, so it is wider than Delete beside it (pixels).
SAVE_WIDTH = 140


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
    ordered = pn.sort_rows(rows, today)
    position = _render_table(ordered)
    render_row_dialog(
        ordered,
        lambda row: row.id,
        position,
        key="pn_dialog",
        table_key=TABLE_KEY,
        title=lambda row: row.publisher_name or row.publisher_id or row.id,
        render=lambda row: _render_detail(row, today),
        actions=lambda row: _render_actions(row, today),
    )
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
            f"{pn.FOLLOW_UP_DAYS} days, a problem past its Slack alert threshold, or a "
            "problem resolved since the publisher was last contacted. Once you have told "
            "the publisher, save the row with that day's date contacted."
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
        pn.RESPONSIBLE,
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


def _render_table(ordered: list[PublisherNotification]) -> int | None:
    st.caption(f"{len(ordered):,} publishers. Select a row to open it.")
    return render_table(
        pn.to_dataframe(ordered),
        pn.tone_frame(ordered),
        columns=pn.COLUMN_SPECS,
        rag_columns=pn.RAG_COLUMNS,
        key=TABLE_KEY,
        empty_message="No publishers match these filters.",
    )


def _render_detail(row: PublisherNotification, today: date) -> None:
    with card("notification_detail"):
        saved = st.session_state.pop(SAVED_KEY, None)
        if saved:
            st.success(saved, icon=":material/check_circle:")
        reasons = pn.alert_reasons(row, today)
        if reasons:
            st.warning(" · ".join(reasons), icon=":material/notifications:")
        if pn.needs_seasonal_check(row):
            st.warning(pn.SEASONAL_CHECK_TEXT, icon=":material/calendar_month:")
        st.caption(
            " · ".join(
                [
                    f"Status: {status_label(row.status) if row.status else '—'}",
                    f"{pn.RESPONSIBLE}: {row.stakeholder or '—'}",
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
        render_edit_fields(row, today)
        render_editable_draft(draft_publisher_email(row, today), key=draft_key(row))


def _prefix(row: PublisherNotification) -> str:
    # Keyed by row and last save: a new row, or a saved one, starts from its stored values.
    return f"pn_edit_{draft_key(row)}"


def render_edit_form(row: PublisherNotification, today: date) -> None:
    """The edit fields with Save under them."""
    render_edit_fields(row, today)
    render_save_button(row, today)


def render_edit_fields(row: PublisherNotification, today: date) -> None:
    """The five steward fields. Not an `st.form`, so Save can sit outside them, in the
    pop-up's fixed footer; Save reads the values back from session state."""
    prefix = _prefix(row)
    st.markdown("**Update this row**")
    status_col, stakeholder_col, contact_col, date_col = st.columns(4)
    status_col.selectbox(
        "Status",
        pn.STATUSES,
        index=pn.STATUSES.index(row.status) if row.status in pn.STATUSES else None,
        format_func=status_label,
        placeholder="Choose a status",
        key=f"{prefix}_status",
    )
    stakeholder_col.text_input(
        pn.RESPONSIBLE, value=row.stakeholder or "", key=f"{prefix}_stakeholder"
    )
    contact_col.text_input("Contact", value=row.contact or "", key=f"{prefix}_contact")
    date_col.date_input(
        "Date contacted",
        value=row.date_contacted,
        max_value=today,
        format="YYYY-MM-DD",
        key=f"{prefix}_date",
    )
    st.text_area("Notes", value=row.notes or "", max_chars=pn.NOTES_MAX, key=f"{prefix}_notes")


def render_save_button(row: PublisherNotification, today: date) -> None:
    """Save the edit fields and save the whole row back."""
    if not st.button("Save", type="primary", key=f"pn_save_{row.id}", width=SAVE_WIDTH):
        return

    prefix = _prefix(row)
    state = st.session_state
    # `date_input` can in principle return a range; this one is a single date or empty.
    picked = state.get(f"{prefix}_date", row.date_contacted)
    contacted = picked if isinstance(picked, date) else None
    body, errors = pn.apply_edits(
        row,
        status=state.get(f"{prefix}_status", row.status) or "",
        stakeholder=state.get(f"{prefix}_stakeholder", row.stakeholder or ""),
        contact=state.get(f"{prefix}_contact", row.contact or ""),
        date_contacted=contacted,
        notes=state.get(f"{prefix}_notes", row.notes or ""),
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

    st.session_state[SAVED_KEY] = f"Saved {row.publisher_name or row.id}."
    # The table selection is kept, so the pop-up reopens on this publisher after the reload.
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


def _render_actions(row: PublisherNotification, today: date) -> None:
    """The pop-up's fixed footer: Save on the left, Delete on the right."""
    if st.session_state.get(f"pn_confirm_delete_{row.id}"):
        _render_delete(row)
        return
    # Right-aligned row: Delete, then Save at the far right.
    with st.container(horizontal=True, horizontal_alignment="right"):
        _render_delete(row)
        render_save_button(row, today)


def _render_delete(row: PublisherNotification) -> None:
    """Delete, with a confirm step in the same pop-up (Streamlit can't open one pop-up
    inside another). Nothing is removed until the steward confirms."""
    asking = f"pn_confirm_delete_{row.id}"
    if not st.session_state.get(asking):
        if st.button(
            "Delete row", icon=":material/delete:", key=f"{DANGER_PREFIX}_pn_delete_{row.id}"
        ):
            st.session_state[asking] = True
            st.rerun(scope="fragment")
        return

    st.error(
        f"This permanently removes **{row.publisher_name or row.id}** from the notification "
        "list, including its problems and notes. It cannot be undone.",
        icon=":material/warning:",
    )
    cancel_col, delete_col = st.columns(2)
    if cancel_col.button("Cancel", key="pn_delete_cancel", width="stretch"):
        st.session_state.pop(asking, None)
        st.rerun(scope="fragment")
    if delete_col.button("Delete", key=f"{DANGER_PREFIX}_pn_delete_confirm", width="stretch"):
        st.session_state.pop(asking, None)
        delete_row(row)
