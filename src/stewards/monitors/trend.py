"""The 30-snapshot trend series for a monitor page."""

from __future__ import annotations

from collections.abc import Sequence

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
