"""A map on a card: the renderer for every choropleth the app draws.

`monitors.choropleth` builds the map and its legend, and `stewards.geography` supplies the
boundaries. This module colours them from the theme and puts them on the page.

The map is explored with the mouse — wheel or pinch to zoom at the cursor, drag to pan,
double-click to zoom in, hover for an area's figures — and the heading's help icon says so.
Two controls cover what a gesture cannot: "Centre on" finds an area by name, which matters
when it is a London borough on a map of England, and "Show the whole map" undoes any amount
of wandering. Both live in an `st.fragment`, so using them reruns the map alone — not the
page's reads, and not a ten-thousand-row table further down.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import streamlit as st

from stewards.components import theme
from stewards.components.surface import card
from stewards.geography import Boundaries
from stewards.monitors.choropleth import Choropleth, ChoroplethStyle

MAP_HELP = (
    "Scroll or pinch to zoom, drag to move, double-click to zoom in. "
    "Hover an area for its figures."
)


def map_style(
    legend_title: str, *, ramp: Sequence[str] = theme.COVERAGE_RAMP
) -> ChoroplethStyle:
    return ChoroplethStyle(
        ramp=ramp,
        no_data=theme.MAP_NO_DATA,
        stroke=theme.MAP_STROKE,
        highlight=theme.MAP_HIGHLIGHT,
        label_colour=theme.MUTED,
        tooltip_background=theme.SURFACE,
        tooltip_text=theme.INK,
        tooltip_border=theme.BORDER,
        legend_title=legend_title,
    )


def focus_key(key: str) -> str:
    """The "centre on" selectbox's key, which is also where the focused area lives."""
    return f"{key}_focus"


def revision_key(key: str) -> str:
    """Bumped whenever the view is set from outside the map. The map sits in a container keyed
    on it, so each bump mounts a fresh map at exactly its opening view. Changing the initial
    view of a mounted map is not enough: deck.gl ignores an unchanged one ("show the whole
    map" when the whole map is where it opened), and Streamlit blends a changed one into
    wherever the user had wandered."""
    return f"{key}_view"


def _new_view(key: str) -> None:
    st.session_state[revision_key(key)] = st.session_state.get(revision_key(key), 0) + 1


def _show_whole(key: str) -> None:
    st.session_state[focus_key(key)] = None
    _new_view(key)


def render_controls(key: str, areas: Boundaries, focus_placeholder: str) -> None:
    """Centre on an area (type to search), and show the whole map."""
    names = areas.names
    focus, whole = st.columns([7, 1], gap="small", vertical_alignment="center")
    focus.selectbox(
        "Centre on",
        sorted(names, key=lambda code: names[code].lower()),
        index=None,
        format_func=lambda code: names.get(code, code),
        placeholder=focus_placeholder,
        key=focus_key(key),
        on_change=_new_view,
        args=(key,),
        label_visibility="collapsed",
    )
    whole.button(
        "",
        icon=":material/zoom_out_map:",
        help="Show the whole map",
        key=f"{key}_whole",
        width="stretch",
        on_click=_show_whole,
        args=(key,),
    )


def render_map(
    key: str,
    title: str,
    build: Callable[[str | None], Choropleth | None],
    *,
    areas: Boundaries,
    height: int,
    focus_placeholder: str = "Centre on an area…",
    caption: str = "",
    attribution: str = "",
    empty_message: str,
) -> None:
    """Title and controls, the legend and the map (or `empty_message` when there is none),
    any caption, and the attribution.

    `build` draws the map opening on an area code, or on everything for None; `areas` are
    the areas it draws, which the "centre on" list offers. The attribution is not optional:
    the open licences the boundaries and the basemap come under require it wherever the map
    is shown.
    """

    @st.fragment
    def body() -> None:
        with card(key):
            figure = build(st.session_state.get(focus_key(key)))
            heading, controls = st.columns([1, 1], vertical_alignment="center")
            heading.subheader(title, anchor=False, divider=False, help=MAP_HELP)
            if figure is None:
                st.caption(empty_message)
                return
            with controls:
                render_controls(key, areas, focus_placeholder)
            st.altair_chart(figure.legend, width="content")
            with st.container(key=f"{key}_map_{st.session_state.get(revision_key(key), 0)}"):
                st.pydeck_chart(figure.deck, height=height)
            if caption:
                st.caption(caption)
            if attribution:
                st.caption(attribution)

    body()
