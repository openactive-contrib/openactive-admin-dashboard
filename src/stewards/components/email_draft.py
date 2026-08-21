"""The draft-email popover. Copy only — the app never sends."""

from __future__ import annotations

from datetime import date

import streamlit as st

from stewards.api.models import Incident
from stewards.monitors.email_draft import draft_email
from stewards.monitors.registry import Monitor


def render_email_draft(monitor: Monitor, incident: Incident, snapshot_date: date) -> None:
    """A popover holding the copyable message for the selected row."""
    label = f"Draft email · {incident.publisher_name}"
    with st.popover(label, width="content"):
        st.caption(
            "Copy this into your mail client. The dashboard does not send email and does not "
            "record that you did."
        )
        st.code(draft_email(monitor, incident, snapshot_date), language="text")
