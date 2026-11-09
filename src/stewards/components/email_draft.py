"""The draft email, shown in the row pop-up. Copy only — the app never sends."""

from __future__ import annotations

from datetime import date

import streamlit as st

from stewards.api.models import Incident, PublisherNotification
from stewards.monitors.email_draft import draft_email
from stewards.monitors.registry import Monitor


def render_email_draft(monitor: Monitor, incident: Incident, snapshot_date: date) -> None:
    """The copyable message for the selected row, shown in full (it sits in the pop-up)."""
    st.markdown("**Draft email**")
    st.caption(
        "Copy this into your mail client with the copy icon. The dashboard does not send "
        "email and does not record that you did."
    )
    st.code(draft_email(monitor, incident, snapshot_date), language="text", wrap_lines=True)


def draft_key(row: PublisherNotification) -> str:
    """Widget key for this row's draft. A new row, or a save, starts a fresh draft."""
    stamp = row.updated_at.isoformat() if row.updated_at else "never"
    return f"notification-draft-{row.id}-{stamp}"


def render_editable_draft(draft: str, *, key: str) -> str:
    """The draft in an editable box; returns what the steward has typed.

    Nothing typed here is saved anywhere. It lives only until the row changes.
    """
    if not draft:
        st.info("No email to draft: none of this publisher's problems can be described yet.")
        return ""
    st.caption(
        "Edit freely, then copy into your mail client. The dashboard does not send email, "
        "and your edits are not saved."
    )
    return st.text_area("Email draft", value=draft, height=420, key=key)
