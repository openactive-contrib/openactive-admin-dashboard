"""The 30-snapshot trend chart."""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from stewards.api.models import TrendPoint
from stewards.components import theme
from stewards.monitors.registry import Monitor
from stewards.monitors.trend import trend_chart


def render_trend(monitor: Monitor, points: Sequence[TrendPoint]) -> None:
    """Solid teal line = open incidents, red line = the subset past the threshold."""
    st.subheader("Open incidents, last 30 daily snapshots", anchor=False, divider=False)
    chart = trend_chart(
        points,
        monitor.threshold_days,
        open_colour=theme.TEAL,
        threshold_colour=theme.RED,
        label_colour=theme.MUTED,
        grid_colour=theme.BORDER_SUBTLE,
    )
    if chart is None:
        st.caption("No trend history for this monitor in the current snapshot window.")
        return

    st.altair_chart(chart, width="stretch")
    st.caption(
        f"The red series is the subset open longer than the {monitor.threshold_days}-day "
        "contact threshold. One point per daily batch."
    )
