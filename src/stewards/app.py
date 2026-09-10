"""Entry point: settings, auth gate, then the grouped navigation.

The gate runs before `st.navigation(...).run()` so no page module is reachable
unauthenticated. Sidebar counts come from the summary endpoint; if it is unavailable the
navigation still renders and the failing page reports the error itself.
"""

from __future__ import annotations

import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.auth.google import render_identity_footer, require_login
from stewards.components import nav
from stewards.components.surface import inject_card_styles
from stewards.config import ConfigError, get_settings
from stewards.monitors.overview import NavBadge, nav_badges
from stewards.monitors.registry import monitor_ids

st.set_page_config(
    page_title="OpenActive Admin Dashboard",
    page_icon=":material/monitor_heart:",
    layout="wide",
)


def _nav_badges() -> dict[str, NavBadge]:
    """Sidebar count pills. An unavailable summary yields no pills, not a broken sidebar.

    The pill tone is the monitor's health, so it is read from the same trend series the
    overview cards use — both reads are cached for the day, so this costs one request per
    monitor per hour, not one per rerun.
    """
    try:
        summary = repository.fetch_summary().data
    except ApiError:
        return {}
    return nav_badges(summary, repository.fetch_monitor_trends(monitor_ids()))


def main() -> None:
    try:
        settings = get_settings()
    except ConfigError as exc:
        st.error(f"**The dashboard is not configured.**\n\n{exc}")
        st.stop()

    # Before the gate, not after: `require_login` stops the script on the sign-in and
    # denied screens, so a stylesheet emitted below it would never reach them and the
    # login card would render as a transparent outline on the canvas tint.
    inject_card_styles()
    email = require_login(settings)
    page = nav.build_navigation()
    nav.render_sidebar(_nav_badges())
    render_identity_footer(email)
    page.run()


main()
