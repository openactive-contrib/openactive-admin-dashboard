"""Trend series shaping."""

from __future__ import annotations

from stewards.api.models import TrendPoint, TrendResponse
from stewards.monitors.trend import OPEN_SERIES, past_threshold_series, trend_frame


def point(day: int, open_count: int, past: int) -> TrendPoint:
    return TrendPoint(
        date=f"2026-08-{day:02d}", open_count=open_count, past_threshold_count=past
    )


def test_frame_has_both_series_indexed_by_date() -> None:
    frame = trend_frame([point(20, 22, 6), point(21, 23, 7)], 7)
    assert list(frame.columns) == [OPEN_SERIES, "Past 7-day threshold"]
    assert list(frame.index) == ["2026-08-20", "2026-08-21"]
    assert frame.iloc[-1][OPEN_SERIES] == 23


def test_out_of_order_points_are_sorted() -> None:
    frame = trend_frame([point(21, 23, 7), point(19, 20, 5), point(20, 22, 6)], 7)
    assert list(frame.index) == ["2026-08-19", "2026-08-20", "2026-08-21"]


def test_empty_series_gives_an_empty_frame_with_both_columns() -> None:
    frame = trend_frame([], 7)
    assert frame.empty
    assert list(frame.columns) == [OPEN_SERIES, "Past 7-day threshold"]


def test_a_single_point_is_a_one_row_frame() -> None:
    assert len(trend_frame([point(21, 1, 0)], 7)) == 1


def test_series_label_states_the_threshold() -> None:
    assert past_threshold_series(14) == "Past 14-day threshold"


def test_sample_trend_covers_thirty_snapshots(stall_trend: TrendResponse) -> None:
    frame = trend_frame(stall_trend.data, 7)
    assert len(frame) == 30
    assert (frame[OPEN_SERIES] >= 0).all()
    assert (frame["Past 7-day threshold"] <= frame[OPEN_SERIES]).all()


def test_empty_trend_fixture_parses_and_shapes(payload) -> None:
    response = TrendResponse.model_validate(payload("trend_empty"))
    assert trend_frame(response.data, 7).empty
