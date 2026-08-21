"""Page chrome shared by every page: header, snapshot caption, footer."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stewards.api.models import Meta
from stewards.components import theme
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import csv_bytes

SNAPSHOT_TIME = "06:00"


def snapshot_caption(meta: Meta) -> str:
    """Stated explicitly on every page so nothing reads as live."""
    return f"Snapshot {meta.snapshot_date.isoformat()} {SNAPSHOT_TIME} · BigQuery daily batch"


def render_header(
    crumb: str,
    title: str,
    meta: Meta,
    *,
    export: pd.DataFrame | None = None,
    export_name: str = "export",
) -> None:
    """Breadcrumb, page title, snapshot caption and the Export CSV button."""
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    with left:
        st.caption(crumb.upper())
        st.title(title, anchor=False)
    with right:
        st.caption(snapshot_caption(meta))
        if export is not None:
            st.download_button(
                "Export CSV",
                data=csv_bytes(export),
                file_name=f"{export_name}_{meta.snapshot_date.isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
                help="Exports the rows currently shown, after filters.",
            )
    st.divider()


def render_error_header(crumb: str, title: str) -> None:
    """Header for a page that could not load. No snapshot caption — there is no snapshot."""
    st.caption(crumb.upper())
    st.title(title, anchor=False)
    st.divider()


def render_footer(query: str, *, note: str = "") -> None:
    st.caption(
        f"Read-only view. Actions available: export, draft publisher email. {note}".strip()
    )
    if query:
        st.caption(f"`{query}`")


def render_sample_data_notice() -> None:
    """Shown when the app is serving bundled payloads instead of the real API."""
    st.warning(
        "Sample data. The monitoring API is not connected, so every figure on this page is "
        "an illustrative payload bundled with the app.",
        icon=":material/science:",
    )


def tone_metric(label: str, value: str, tone: Tone) -> None:
    """A KPI whose value carries the semantic colour, using Streamlit's markdown colours."""
    st.caption(label.upper())
    st.markdown(f"## :{theme.markdown_colour(tone)}[{value}]")
