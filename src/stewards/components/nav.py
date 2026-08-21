"""Navigation: grouped sections built from the registry, with open-incident count badges.

The page objects are held here so the overview tiles can navigate to a monitor by id
without app.py having to pass them down.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import streamlit as st
from streamlit.navigation.page import StreamlitPage

from stewards.monitors.overview import nav_label
from stewards.monitors.registry import MONITOR_REGISTRY, groups

#: Page paths are resolved against the package, not the working directory, so the
#: navigation builds identically however the app was launched.
APP_DIR = Path(__file__).resolve().parent.parent

OVERVIEW_PAGE = "pages/00_overview.py"
CONTACT_QUEUE_PAGE = "pages/01_contact_queue.py"
DOCS_PAGE = "pages/90_docs.py"

_pages: dict[str, StreamlitPage] = {}


def build_navigation(counts: Mapping[str, int], past_threshold: int | None) -> StreamlitPage:
    """Build the grouped sidebar and return the selected page."""
    _pages.clear()
    _pages["overview"] = st.Page(
        APP_DIR / OVERVIEW_PAGE, title="Monitor overview", default=True
    )
    _pages["contact_queue"] = st.Page(
        APP_DIR / CONTACT_QUEUE_PAGE, title=nav_label("Contact queue", past_threshold)
    )
    sections: dict[str, list[StreamlitPage]] = {
        "Overview": [_pages["overview"], _pages["contact_queue"]]
    }

    for group in groups():
        section: list[StreamlitPage] = []
        for monitor in MONITOR_REGISTRY:
            if monitor.group is not group:
                continue
            page = st.Page(
                APP_DIR / monitor.page, title=nav_label(monitor.name, counts.get(monitor.id))
            )
            _pages[monitor.id] = page
            section.append(page)
        sections[group.value] = section

    _pages["docs"] = st.Page(APP_DIR / DOCS_PAGE, title="Documentation")
    sections["Knowledge base"] = [_pages["docs"]]

    return st.navigation(sections)


def page_for(key: str) -> StreamlitPage | None:
    """Look up a built page by monitor id, or by `overview`/`contact_queue`/`docs`."""
    return _pages.get(key)


def switch_to(key: str) -> None:
    """Navigate to a page built in this run. A missing key is a no-op, not a crash."""
    page = page_for(key)
    if page is not None:
        st.switch_page(page)
