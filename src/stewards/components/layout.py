"""Page chrome shared by every page: the header bar, KPI blocks, footer.

Structure is Streamlit elements; the type scale lives in `components/surface.py`, keyed off
the container keys this module sets. Nothing here interpolates data into HTML.
"""

from __future__ import annotations

import streamlit as st

from stewards.api.models import Meta
from stewards.components import theme
from stewards.components.surface import card
from stewards.monitors.thresholds import Tone

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
    meta: Meta,
) -> None:
    """The header bar: breadcrumb and title left, snapshot provenance right."""
    lines = (f"Snapshot `{meta.snapshot_date.isoformat()} {SNAPSHOT_TIME}`", SOURCE_LINE)

    with card("header"):
        titles, provenance = st.columns([5, 3], vertical_alignment="center")
        with titles:
            _render_titles(crumb, title)
        with provenance, st.container(key="oasnapshot"):
            if lines[0]:
                st.markdown(lines[0])
            if lines[1]:
                with st.container(key="oasnapshotsource"):
                    st.markdown(lines[1])


def render_error_header(crumb: str, title: str) -> None:
    """Header for a page that could not load. No snapshot caption — there is no snapshot."""
    with card("header"):
        _render_titles(crumb, title)


def render_footer(query: str, *, note: str = "") -> None:
    st.caption(f"Read-only view. Actions available: draft publisher email. {note}".strip())
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
