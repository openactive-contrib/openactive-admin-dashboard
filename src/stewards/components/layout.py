"""Page chrome shared by every page: the header bar, KPI blocks, footer.

Structure is Streamlit elements; the type scale lives in `components/surface.py`, keyed off
the container keys this module sets. Nothing here interpolates data into HTML.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stewards.api.models import Meta
from stewards.components import theme
from stewards.components.surface import card
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import csv_bytes

SNAPSHOT_TIME = "06:00"
SOURCE_LINE = "BigQuery · daily batch"


def _render_titles(crumb: str, title: str) -> None:
    with st.container(key="oacrumb"):
        st.markdown(crumb.upper())
    with st.container(key="oatitle"):
        st.markdown(title)


def render_header(
    crumb: str,
    title: str,
    meta: Meta | None = None,
    *,
    export: pd.DataFrame | None = None,
    export_name: str = "export",
    meta_lines: tuple[str, str] | None = None,
    export_stamp: str = "",
) -> None:
    """The header bar: breadcrumb and title left, provenance and Export CSV right.

    `meta` renders the snapshot lines and stamps the export filename. Pages not backed by the
    daily batch pass `meta_lines` instead — the knowledge base is files, not a snapshot, and
    labelling it "BigQuery · daily batch" would be untrue.
    """
    if meta is not None:
        lines = (f"Snapshot `{meta.snapshot_date.isoformat()} {SNAPSHOT_TIME}`", SOURCE_LINE)
        stamp = meta.snapshot_date.isoformat()
    else:
        lines = meta_lines or ("", "")
        stamp = export_stamp

    with card("header"):
        titles, provenance, action = st.columns([5, 2, 1], vertical_alignment="center")
        with titles:
            _render_titles(crumb, title)
        with provenance, st.container(key="oasnapshot"):
            if lines[0]:
                st.markdown(lines[0])
            if lines[1]:
                with st.container(key="oasnapshotsource"):
                    st.markdown(lines[1])
        with action:
            if export is not None:
                st.download_button(
                    "Export CSV",
                    data=csv_bytes(export),
                    file_name=f"{export_name}_{stamp}.csv" if stamp else f"{export_name}.csv",
                    mime="text/csv",
                    icon=":material/download:",
                    width="stretch",
                    help="Exports the rows currently shown, after filters.",
                )


def render_error_header(crumb: str, title: str) -> None:
    """Header for a page that could not load. No snapshot caption — there is no snapshot."""
    with card("header"):
        _render_titles(crumb, title)


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


def tone_metric(
    label: str,
    value: str,
    tone: Tone | None = None,
    *,
    slug: str,
    delta: str | None = None,
    sub: str | None = None,
) -> None:
    """A KPI: small-caps label, large value, inline delta, sub-line.

    `tone` colours the value; None leaves it in body ink, for a figure that is context
    rather than a state. `slug` must be unique on the page — it becomes the container key
    the type scale hooks on.
    """
    if label:
        with st.container(key=f"oakpilabel_{slug}"):
            st.markdown(label.upper())

    coloured = f":{theme.markdown_colour(tone)}[{value}]" if tone is not None else value
    with st.container(horizontal=True, vertical_alignment="bottom", key=f"oakpirow_{slug}"):
        with st.container(key=f"oakpivalue_{slug}", width="content"):
            st.markdown(coloured)
        if delta is not None:
            with st.container(key=f"oakpidelta_{slug}", width="content"):
                st.markdown(delta)

    if sub is not None:
        with st.container(key=f"oakpisub_{slug}"):
            st.markdown(sub)
