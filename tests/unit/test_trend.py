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


# --- charts -------------------------------------------------------------------------------


def test_sparkline_needs_at_least_two_points() -> None:
    from stewards.monitors.trend import sparkline_chart

    assert sparkline_chart([], "#0E8F8A") is None
    assert sparkline_chart([5], "#0E8F8A") is None
    assert sparkline_chart([5, 6], "#0E8F8A") is not None


def test_sparkline_has_no_axes_and_no_background() -> None:
    from stewards.monitors.trend import sparkline_chart

    spec = sparkline_chart([1, 2, 3], "#C6413B").to_dict()
    assert spec["encoding"]["x"]["axis"] is None
    assert spec["encoding"]["y"]["axis"] is None
    assert spec["config"]["background"] == "transparent"
    assert spec["mark"]["color"] == "#C6413B"


def test_trend_chart_is_none_without_points() -> None:
    from stewards.monitors.trend import trend_chart

    assert trend_chart([], 7, "#0E8F8A", "#C6413B", "#5C6B76", "#EDF0F2") is None


def test_trend_chart_dashes_the_past_threshold_series() -> None:
    """The brief specifies a solid open series and a dashed past-threshold series."""
    from stewards.monitors.trend import trend_chart

    chart = trend_chart(
        [point(20, 22, 6), point(21, 23, 7)], 7, "#0E8F8A", "#C6413B", "#5C6B76", "#EDF0F2"
    )
    assert chart is not None
    spec = chart.to_dict()
    dash = spec["encoding"]["strokeDash"]["scale"]
    assert dash["domain"] == [OPEN_SERIES, "Past 7-day threshold"]
    assert dash["range"] == [[1, 0], [4, 3]]  # solid, then dashed
    colour = spec["encoding"]["color"]["scale"]
    assert colour["range"] == ["#0E8F8A", "#C6413B"]
    assert spec["config"]["background"] == "transparent"


def test_trend_chart_plots_both_series_for_every_point(stall_trend: TrendResponse) -> None:
    from stewards.monitors.trend import trend_chart

    chart = trend_chart(stall_trend.data, 7, "#0E8F8A", "#C6413B", "#5C6B76", "#EDF0F2")
    assert chart is not None
    frame = chart.data
    assert len(frame) == 60  # 30 snapshots x 2 series
    assert set(frame["series"]) == {OPEN_SERIES, "Past 7-day threshold"}
