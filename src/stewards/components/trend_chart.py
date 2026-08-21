"""The 30-snapshot trend chart."""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from stewards.api.models import TrendPoint
from stewards.components import theme
from stewards.monitors.registry import Monitor
from stewards.monitors.trend import OPEN_SERIES, past_threshold_series, trend_frame


def render_trend(monitor: Monitor, points: Sequence[TrendPoint]) -> None:
    """Solid teal line = open incidents, red line = the subset past the threshold."""
    st.subheader("Open incidents, last 30 daily snapshots", anchor=False, divider=False)
    if not points:
        st.caption("No trend history for this monitor in the current snapshot window.")
        return

    frame = trend_frame(points, monitor.threshold_days)
    st.line_chart(
        frame,
        y=[OPEN_SERIES, past_threshold_series(monitor.threshold_days)],
        color=[theme.TEAL, theme.RED],
        height=240,
        use_container_width=True,
    )
    st.caption(
        f"The red series is the subset open longer than the {monitor.threshold_days}-day "
        "contact threshold. One point per daily batch."
    )
