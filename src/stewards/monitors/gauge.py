"""A figure read against a fixed benchmark, for a monitor with no daily series.

`monitors.health` needs history: it asks how fast a series is moving and whether the latest
point stands out from its own recent level. A monitor whose batch reports only this
snapshot's number has neither, so the reference has to come from outside the data — a
benchmark declared on the registry entry. That one number decides both the state chip and
the meter, so the two can never tell different stories.

The form is a meter rather than a dial: the reader's job is "is this figure above or below
the benchmark", which position on a track answers at a glance and at tile size. Pure — no
Streamlit, and the colours are passed in by whoever draws it, as in `monitors.trend`.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from stewards.monitors.health import Health, HealthState, Movement
from stewards.monitors.tile_viz import Gauge

#: The track spans this many benchmarks, putting the benchmark at the midpoint: a figure
#: that has doubled fills the track, and one at half the benchmark fills a quarter of it.
TRACK_BENCHMARKS = 2.0

METER_HEIGHT = 30
BAR_HEIGHT = 12
PAGE_METER_HEIGHT = 56
PAGE_BAR_HEIGHT = 22

#: A true minus sign is used elsewhere for deltas; this module only ever states a magnitude
#: alongside the words "above" or "below", so it needs no sign.
_LEVEL = "level with"


def track_span(spec: Gauge) -> float:
    """The full width of the meter in the monitor's own units."""
    return TRACK_BENCHMARKS * spec.benchmark


def benchmark_ratio(count: int | None, spec: Gauge) -> float | None:
    """The figure as a multiple of the benchmark, or None when it was not reported."""
    return None if count is None else count / spec.benchmark


def gauge_fraction(count: int | None, spec: Gauge) -> float | None:
    """How much of the track is filled, in `[0, 1]`. The benchmark sits at 0.5.

    Clamped, so a figure past twice the benchmark fills the track rather than overflowing
    it: the caption still states the real multiple.
    """
    if count is None:
        return None
    return min(max(count / track_span(spec), 0.0), 1.0)


def benchmark_phrase(count: int | None, spec: Gauge) -> str:
    """The figure, the benchmark, and the distance between them, in words."""
    benchmark = f"{spec.benchmark:,.0f}"
    if count is None:
        return f"not reported this snapshot · benchmark {benchmark}"
    ratio = count / spec.benchmark
    gap = abs(ratio - 1.0)
    if gap < 0.0005:
        return f"{count:,} · {_LEVEL} the {benchmark} benchmark"
    side = "above" if ratio > 1.0 else "below"
    return f"{count:,} · {gap:.1%} {side} the {benchmark} benchmark"


def assess_benchmark(count: int | None, spec: Gauge) -> Health:
    """The card's state, taken from the benchmark rather than from a trend.

    Movement is always unknown: one number carries a level but says nothing about direction,
    and claiming otherwise is exactly the fabrication the null-count rules exist to avoid.
    """
    reason = benchmark_phrase(count, spec)
    if count is None:
        return Health(HealthState.UNKNOWN, Movement.UNKNOWN, reason, baseline=spec.benchmark)
    ratio = count / spec.benchmark
    if ratio > spec.critical_ratio:
        state = HealthState.CRITICAL
    elif ratio > spec.warn_ratio:
        state = HealthState.WARNING
    else:
        state = HealthState.HEALTHY
    return Health(
        state,
        Movement.UNKNOWN,
        reason,
        current=float(count),
        baseline=spec.benchmark,
    )


def gauge_caption(count: int | None, spec: Gauge) -> str:
    """The line under the meter, in place of a card's trend note."""
    return benchmark_phrase(count, spec)


def _scale(spec: Gauge) -> alt.Scale:
    return alt.Scale(domain=[0.0, track_span(spec)], nice=False, zero=True)


def gauge_chart(
    count: int | None,
    spec: Gauge,
    *,
    value_colour: str,
    track_colour: str,
    benchmark_colour: str,
    height: int = METER_HEIGHT,
    bar_height: int = BAR_HEIGHT,
) -> alt.LayerChart | None:
    """A meter: the filled figure over its track, with the benchmark marked at the midpoint.

    None when the figure was not reported, matching `trend.sparkline_chart` — the caller
    draws nothing rather than an empty axis. The filled end is rounded and the baseline end
    square, so the bar reads as growing from zero.
    """
    if count is None:
        return None
    scale = _scale(spec)
    axis = alt.X("v:Q", axis=None, scale=scale)
    track = (
        alt.Chart(pd.DataFrame({"v": [track_span(spec)]}))
        .mark_bar(color=track_colour, height=bar_height, cornerRadius=bar_height / 2)
        .encode(x=axis)
    )
    value = (
        alt.Chart(pd.DataFrame({"v": [min(float(count), track_span(spec))]}))
        .mark_bar(
            color=value_colour,
            height=bar_height,
            cornerRadiusTopRight=bar_height / 2,
            cornerRadiusBottomRight=bar_height / 2,
        )
        .encode(x=axis)
    )
    benchmark = (
        alt.Chart(pd.DataFrame({"v": [spec.benchmark]}))
        .mark_rule(color=benchmark_colour, strokeWidth=2, strokeCap="round")
        .encode(x=axis)
    )
    chart: alt.LayerChart = (
        alt.layer(track, value, benchmark)
        .properties(height=height)
        .configure_view(strokeWidth=0, fill=None)
        .configure_axis(grid=False, domain=False)
        .configure(background="transparent", padding=0)
    )
    return chart
