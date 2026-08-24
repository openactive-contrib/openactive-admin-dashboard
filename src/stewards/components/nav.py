"""Navigation.

Streamlit's own sidebar nav renders a plain-text label per page, so it cannot show the
count pill the design calls for. The built-in nav is therefore hidden and the sidebar is
drawn here from the registry: `st.page_link` for the row, `st.badge` for the count. Both are
native widgets, and the badge colours resolve through the RAG slots in `config.toml`.

The page objects are held in this module so the overview tiles can link to a monitor by id
without `app.py` having to pass them down.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import streamlit as st
from streamlit.navigation.page import StreamlitPage

from stewards.components import theme
from stewards.config import get_settings
from stewards.monitors.overview import NavBadge
from stewards.monitors.registry import MONITOR_REGISTRY, groups

#: Page paths are resolved against the package, not the working directory, so the
#: navigation builds identically however the app was launched.
APP_DIR = Path(__file__).resolve().parent.parent

#: The OpenActive lockup. Its wordmark is navy on transparency, which reads on the light
#: canvas but not on the dark sidebar — `surface.py` puts a light plaque behind the sidebar
#: copy rather than altering the artwork.
ASSET_DIR = APP_DIR / "assets"
LOGO = ASSET_DIR / "openactive-logo.png"

OVERVIEW_PAGE = "views/00_overview.py"
CONTACT_QUEUE_PAGE = "views/01_contact_queue.py"

OVERVIEW_SECTION = "Overview"

#: The runbooks live on GitHub Pages, outside the app. `st.page_link` renders a URL as an
#: external link, which opens in a new tab; the URL comes from `STEWARDS_DOCS_URL`.
REFERENCE_SECTION = "Reference"
DOCS_LABEL = "Documentation"

_pages: dict[str, StreamlitPage] = {}
_sections: dict[str, list[tuple[str, StreamlitPage]]] = {}


def build_navigation() -> StreamlitPage:
    """Build the page set and return the selected page.

    The sidebar itself is drawn by `render_sidebar`, which needs the counts; keeping the two
    apart means the navigation still resolves when the summary endpoint is unavailable.
    """
    _pages.clear()
    _sections.clear()

    _pages["overview"] = st.Page(
        APP_DIR / OVERVIEW_PAGE, title="Monitor overview", default=True
    )
    _pages["contact_queue"] = st.Page(APP_DIR / CONTACT_QUEUE_PAGE, title="Contact queue")
    _sections[OVERVIEW_SECTION] = [
        ("overview", _pages["overview"]),
        ("contact_queue", _pages["contact_queue"]),
    ]

    for group in groups():
        section: list[tuple[str, StreamlitPage]] = []
        for monitor in MONITOR_REGISTRY:
            if monitor.group is not group:
                continue
            page = st.Page(APP_DIR / monitor.page, title=monitor.name)
            _pages[monitor.id] = page
            section.append((monitor.id, page))
        _sections[group.value] = section

    return st.navigation(
        {name: [page for _, page in items] for name, items in _sections.items()},
        position="hidden",
    )


def render_logo() -> None:
    """Pin the OpenActive mark above everything else in the sidebar.

    `st.logo` is app chrome rather than a flow element, so it lands at the top of the
    sidebar whatever order it is called in. No `icon_image`: the same lockup is what
    Streamlit shows in the app's top-left while the sidebar is collapsed, and the navy
    wordmark has 9.4:1 against the canvas there.
    """
    st.logo(str(LOGO), size="medium")


def render_sidebar(badges: Mapping[str, NavBadge]) -> None:
    """The grouped sidebar: logo, section heading, page link, count pill."""
    render_logo()
    with st.sidebar:
        for section, items in _sections.items():
            st.caption(section.upper())
            for key, page in items:
                badge = badges.get(key)
                row = st.container(
                    horizontal=True,
                    horizontal_alignment="distribute",
                    vertical_alignment="center",
                )
                with row:
                    st.page_link(page, label=page.title)
                    if badge is not None:
                        st.badge(badge.text, color=theme.markdown_colour(badge.tone))
        st.caption(REFERENCE_SECTION.upper())
        st.page_link(
            get_settings().docs_url,
            label=DOCS_LABEL,
            icon=":material/open_in_new:",
        )


def page_for(key: str) -> StreamlitPage | None:
    """Look up a built page by monitor id, or by `overview`/`contact_queue`/`docs`."""
    return _pages.get(key)


def switch_to(key: str) -> None:
    """Navigate to a page built in this run. A missing key is a no-op, not a crash."""
    page = page_for(key)
    if page is not None:
        st.switch_page(page)
