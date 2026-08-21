"""The 30-snapshot trend series for a monitor page."""

from __future__ import annotations

from collections.abc import Sequence

import altair as alt
import pandas as pd

from stewards.api.models import TrendPoint

OPEN_SERIES = "Open incidents"


def past_threshold_series(threshold_days: int) -> str:
    return f"Past {threshold_days}-day threshold"


def trend_frame(points: Sequence[TrendPoint], threshold_days: int) -> pd.DataFrame:
    """Date-indexed frame with the open series and the past-threshold subset.

    Points are sorted by date so an out-of-order API response still charts left to right.
    An empty series yields an empty frame with both columns present.
    """
    columns = [OPEN_SERIES, past_threshold_series(threshold_days)]
    frame = pd.DataFrame(
        [
            {
                "date": point.date.isoformat(),
                OPEN_SERIES: point.open_count,
                columns[1]: point.past_threshold_count,
            }
            for point in sorted(points, key=lambda p: p.date)
        ],
        columns=["date", *columns],
    )
    return frame.set_index("date")


SPARKLINE_HEIGHT = 38
MIN_SPARKLINE_POINTS = 2


def sparkline_chart(
    values: Sequence[float], colour: str, height: int = SPARKLINE_HEIGHT
) -> alt.Chart | None:
    """An axis-less trend line for a monitor tile.

    Returns None for a series too short to draw, so the caller renders nothing rather than an
    empty axis frame. Altair rather than `st.line_chart` because a tile sparkline carries no
    axes, grid or labels — only the shape.
    """
    if len(values) < MIN_SPARKLINE_POINTS:
        return None
    frame = pd.DataFrame({"i": range(len(values)), "v": list(values)})
    chart: alt.Chart = (
        alt.Chart(frame)
        .mark_line(color=colour, strokeWidth=1.8, interpolate="monotone")
        .encode(
            x=alt.X("i:Q", axis=None, scale=alt.Scale(nice=False, padding=0)),
            y=alt.Y("v:Q", axis=None, scale=alt.Scale(nice=False, zero=False, padding=2)),
        )
        .properties(height=height)
        .configure_view(strokeWidth=0, fill=None)
        .configure_axis(grid=False, domain=False)
        .configure(background="transparent", padding=0)
    )
    return chart


TREND_HEIGHT = 240


def trend_chart(
    points: Sequence[TrendPoint],
    threshold_days: int,
    open_colour: str,
    threshold_colour: str,
    label_colour: str,
    grid_colour: str,
    height: int = TREND_HEIGHT,
) -> alt.Chart | None:
    """The 30-snapshot trend: solid line for open incidents, dashed for past threshold.

    Altair rather than `st.line_chart` for two reasons the brief asks for and the built-in
    chart cannot express: a dashed series, and a transparent plot area so the chart sits on
    the white card rather than in a grey panel.
    """
    if not points:
        return None
    past_label = past_threshold_series(threshold_days)
    frame = trend_frame(points, threshold_days).reset_index()
    long = frame.melt("date", var_name="series", value_name="count")

    chart: alt.Chart = (
        alt.Chart(long)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X(
                "date:O",
                title=None,
                axis=alt.Axis(labelAngle=-90, labelOverlap=True, tickCount=6, grid=False),
            ),
            y=alt.Y(
                "count:Q",
                title=None,
                scale=alt.Scale(zero=True, nice=True),
                axis=alt.Axis(tickCount=4, grid=True),
            ),
            color=alt.Color(
                "series:N",
                title=None,
                scale=alt.Scale(
                    domain=[OPEN_SERIES, past_label],
                    range=[open_colour, threshold_colour],
                ),
                legend=alt.Legend(orient="bottom", direction="horizontal", symbolType="stroke"),
            ),
            strokeDash=alt.StrokeDash(
                "series:N",
                title=None,
                scale=alt.Scale(domain=[OPEN_SERIES, past_label], range=[[1, 0], [4, 3]]),
                legend=None,
            ),
        )
        .properties(height=height)
        .configure_view(strokeWidth=0, fill=None)
        .configure(background="transparent")
        .configure_axis(
            domain=False,
            labelColor=label_colour,
            tickColor=grid_colour,
            gridColor=grid_colour,
        )
    )
    return chart
