"""The 30-snapshot trend chart, or the benchmark meter for a monitor with no history."""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from stewards.api.models import TrendPoint
from stewards.components import theme
from stewards.monitors.gauge import (
    PAGE_BAR_HEIGHT,
    PAGE_METER_HEIGHT,
    assess_benchmark,
    gauge_caption,
    gauge_chart,
)
from stewards.monitors.health import TONES
from stewards.monitors.registry import Monitor
from stewards.monitors.tile_viz import Gauge
from stewards.monitors.trend import trend_chart


def render_benchmark(monitor: Monitor, spec: Gauge, count: int | None) -> None:
    """The same meter the overview card draws, at page scale.

    A monitor whose batch reports no series would otherwise get an empty chart slot on its
    own page, which reads as a fault rather than as the absence of history it is.
    """
    st.subheader(
        f"This snapshot against a benchmark of {spec.benchmark:,.0f}",
        anchor=False,
        divider=False,
    )
    tone = TONES[assess_benchmark(count, spec).state]
    chart = gauge_chart(
        count,
        spec,
        value_colour=theme.FOREGROUND[tone],
        track_colour=theme.SURFACE_SUNKEN,
        benchmark_colour=theme.INK_SOFTER,
        height=PAGE_METER_HEIGHT,
        bar_height=PAGE_BAR_HEIGHT,
    )
    if chart is None:
        st.caption("This snapshot does not report a figure for this monitor.")
        return
    st.altair_chart(chart, width="stretch")
    st.caption(
        f"{gauge_caption(count, spec)}. The mark sits at the benchmark, and the track runs "
        "to twice it. The daily batch reports no history for this monitor yet, so there is "
        "no trend to judge."
    )


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


def render_figure(monitor: Monitor, points: Sequence[TrendPoint], count: int | None) -> None:
    """Whichever of the two the monitor declares, so the page holds no branch of its own."""
    match monitor.viz:
        case Gauge() as spec:
            render_benchmark(monitor, spec, count)
        case _:
            render_trend(monitor, points)
