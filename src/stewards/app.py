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

st.set_page_config(
    page_title="OpenActive Admin Dashboard",
    page_icon=":material/monitor_heart:",
    layout="wide",
)


def _nav_badges() -> dict[str, NavBadge]:
    """Sidebar count pills. An unavailable summary yields no pills, not a broken sidebar."""
    try:
        return nav_badges(repository.fetch_summary().data)
    except ApiError:
        return {}


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
