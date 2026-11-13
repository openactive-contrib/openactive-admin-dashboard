"""The ecosystem growth series: table rows, chart frame and chart."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import pytest

from fixture_loader import load_sample
from stewards.api.models import EcosystemGrowthPeriod, EcosystemGrowthResponse, GrowthDataset
from stewards.monitors import ecosystem
from stewards.monitors.ecosystem import (
    COMPLETE,
    DEFAULT_SERIES,
    DETAIL_COLUMNS,
    IN_PROGRESS,
    LISTED_SERIES,
    SERIES,
    TABLE_COLUMNS,
    chart_frame,
    dataset_list,
    detail_frame,
    dialog_caption,
    dialog_title,
    growth_chart,
    label_for,
    list_tab_label,
    month_label,
    newest_first,
    series_for,
    table_frame,
)
from stewards.monitors.transforms import EMPTY

COLOURS = {s.field: "#0E8F8A" for s in SERIES}
STYLE: dict[str, Any] = {
    "colours": COLOURS,
    "label_colour": "#5C6B76",
    "grid_colour": "#EDF0F2",
    "surface_colour": "#FFFFFF",
}


@pytest.fixture
def periods() -> tuple[EcosystemGrowthPeriod, ...]:
    return EcosystemGrowthResponse.model_validate(load_sample("ecosystem_growth")).data


def period(**overrides: Any) -> EcosystemGrowthPeriod:
    payload: dict[str, Any] = {"period_start": "2026-11-01", "period_end": "2026-11-30"}
    return EcosystemGrowthPeriod.model_validate(payload | overrides)


# --- series -------------------------------------------------------------------------------


def test_every_series_is_a_count_on_the_model() -> None:
    fields = EcosystemGrowthPeriod.model_fields
    for series in SERIES:
        assert series.field in fields
        assert fields[series.field].annotation == int | None


def test_series_labels_are_unique_and_the_defaults_are_series() -> None:
    labels = [s.label for s in SERIES]
    assert len(labels) == len(set(labels))
    assert set(DEFAULT_SERIES) <= {s.field for s in SERIES}


def test_label_for_round_trips_and_rejects_an_unknown_field() -> None:
    assert label_for("datasets_crawled") == "Datasets crawled"
    with pytest.raises(KeyError, match="unknown growth series"):
        label_for("datasets_deleted")


def test_the_blurb_is_factual_copy() -> None:
    assert len(ecosystem.BLURB) > 80
    assert "!" not in ecosystem.BLURB


# --- ordering and labels ------------------------------------------------------------------


def test_month_label_is_the_iso_month() -> None:
    assert month_label(period()) == "2026-11"


def test_newest_first_orders_by_period_start(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    starts = [p.period_start for p in newest_first(reversed(periods))]
    assert starts == sorted(starts, reverse=True)
    assert starts[0] == date(2026, 11, 1)


def test_newest_first_of_nothing_is_empty() -> None:
    assert newest_first([]) == []


# --- table --------------------------------------------------------------------------------


def test_the_table_lists_every_month_most_recent_first(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    frame = table_frame(periods)
    assert list(frame.columns) == [col.label for col in TABLE_COLUMNS]
    assert list(frame["Month"]) == [
        "2026-11",
        "2026-10",
        "2026-09",
        "2026-08",
        "2026-07",
        "2026-06",
        "2026-05",
    ]
    assert list(frame["Status"]) == [IN_PROGRESS, *[COMPLETE] * 6]
    assert frame.loc[0, "Datasets crawled"] == 173
    assert frame.loc[0, "Days with data"] == 13
    assert frame.loc[0, "Not ingested"] == 6
    assert frame.loc[0, "Without future"] == 18


def test_the_table_shows_every_tabled_figure_and_nothing_else() -> None:
    labels = {col.label for col in TABLE_COLUMNS}
    assert {s.label for s in SERIES if s.in_table} | {"Days with data"} <= labels
    assert "With future" not in labels  # charted, not tabled
    assert "Days with feed data" not in labels
    assert TABLE_COLUMNS[0].primary


def test_a_null_figure_is_blank_and_a_reported_zero_is_zero(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    frame = table_frame(periods).set_index("Month")
    assert pd.isna(frame.loc["2026-05", "Added"])
    assert pd.isna(frame.loc["2026-07", "Not ingested"])
    assert frame.loc["2026-11", "Added"] == 0


def test_an_empty_series_is_an_empty_table_with_its_columns() -> None:
    frame = table_frame([])
    assert frame.empty
    assert list(frame.columns) == [col.label for col in TABLE_COLUMNS]


def test_a_month_reporting_nothing_but_its_dates_renders_blank() -> None:
    frame = table_frame([period()])
    assert len(frame) == 1
    assert frame.loc[0, "Status"] == COMPLETE
    assert frame.drop(columns=["Month", "Status"]).isna().all(axis=None)


# --- chart --------------------------------------------------------------------------------


def test_the_chart_frame_is_oldest_first_with_one_row_per_month_per_series(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    frame = chart_frame(periods, ["datasets_crawled", "datasets_stopped"])
    assert len(frame) == 7 + 4  # stopped is null before 2026-08
    assert list(frame["month"].unique()) == [
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
        "2026-11",
    ]
    assert set(frame["series"]) == {"Datasets crawled", "Stopped"}
    assert frame[frame["in_progress"]]["month"].unique().tolist() == ["2026-11"]


def test_the_chart_frame_drops_a_null_figure_rather_than_plotting_zero(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    frame = chart_frame(periods, ["datasets_added"])
    assert list(frame["month"]) == [
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
        "2026-11",
    ]
    assert list(frame["value"]) == [2, 3, 2, 1, 1, 0]


def test_the_chart_frame_of_nothing_keeps_its_columns() -> None:
    frame = chart_frame([], DEFAULT_SERIES)
    assert frame.empty
    assert list(frame.columns) == ["month", "series", "value", "status", "in_progress"]


def test_the_chart_draws_only_the_chosen_series(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    chart = growth_chart(periods, DEFAULT_SERIES, **STYLE)
    assert chart is not None
    spec = chart.to_dict()
    colour = spec["layer"][0]["encoding"]["color"]
    assert colour["scale"]["domain"] == ["Datasets crawled", "With future", "Stopped"]
    assert spec["layer"][0]["mark"]["type"] == "line"
    assert spec["layer"][1]["mark"]["type"] == "point"


def test_a_month_in_progress_gets_a_hollow_point(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    chart = growth_chart(periods, ["datasets_crawled"], **STYLE)
    assert chart is not None
    fill = chart.to_dict()["layer"][1]["encoding"]["fill"]
    assert fill["condition"] == {"test": "datum.in_progress", "value": "#FFFFFF"}


def test_a_dashed_series_is_dashed_and_a_solid_one_is_not(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    chart = growth_chart(periods, ["datasets_stopped", "datasets_added"], **STYLE)
    assert chart is not None
    dash = chart.to_dict()["layer"][0]["encoding"]["strokeDash"]["scale"]
    assert dash["domain"] == ["Stopped", "Added"]
    assert dash["range"] == [[1, 0], [4, 3]]


def test_no_chart_without_periods_or_series(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    assert growth_chart([], DEFAULT_SERIES, **STYLE) is None
    assert growth_chart(periods, [], **STYLE) is None


def test_no_chart_when_every_chosen_figure_is_null() -> None:
    assert growth_chart([period()], DEFAULT_SERIES, **STYLE) is None


def test_a_single_month_still_charts() -> None:
    chart = growth_chart(
        [period(datasets_crawled=160, in_progress=True)], DEFAULT_SERIES, **STYLE
    )
    assert chart is not None


# --- the month pop-up ---------------------------------------------------------------------


def by_month(periods: tuple[EcosystemGrowthPeriod, ...], month: str) -> EcosystemGrowthPeriod:
    return next(p for p in periods if month_label(p) == month)


def dataset(**overrides: Any) -> GrowthDataset:
    payload: dict[str, Any] = {"dataset_url": "https://a.example/OpenActive/"}
    return GrowthDataset.model_validate(payload | overrides)


def test_every_list_field_is_a_list_on_the_model() -> None:
    fields = EcosystemGrowthPeriod.model_fields
    for series in LISTED_SERIES:
        assert series.list_field is not None
        assert fields[series.list_field].annotation == tuple[GrowthDataset, ...] | None


def test_the_pop_up_lists_every_figure_the_api_names_datasets_for() -> None:
    assert [s.label for s in LISTED_SERIES] == [
        "Added",
        "Removed",
        "Without future",
        "Not ingested",
        "Stopped",
        "Stopped (all runs failed)",
    ]


def test_a_list_reads_as_sent(periods: tuple[EcosystemGrowthPeriod, ...]) -> None:
    november = by_month(periods, "2026-11")
    assert dataset_list(november, series_for("datasets_added")) == ()
    removed = dataset_list(november, series_for("datasets_removed"))
    assert removed is not None
    assert [d.publisher_name for d in removed] == ["Leisure SK"]


def test_a_list_the_batch_did_not_compute_is_none_not_empty(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    assert dataset_list(by_month(periods, "2026-05"), series_for("datasets_added")) is None


def test_a_series_without_a_list_has_none() -> None:
    assert dataset_list(period(), series_for("datasets_crawled")) is None


def test_every_list_in_the_fixture_matches_its_count(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    for month in periods:
        for series in LISTED_SERIES:
            datasets = dataset_list(month, series)
            count = getattr(month, series.field)
            assert (datasets is None) == (count is None)
            assert datasets is None or len(datasets) == count


def test_a_tab_label_carries_the_count_and_null_reads_em_dash() -> None:
    added = series_for("datasets_added")
    assert list_tab_label(period(datasets_added=2), added) == "Added · 2"
    assert list_tab_label(period(datasets_added=0), added) == "Added · 0"
    assert list_tab_label(period(), added) == f"Added · {EMPTY}"
    assert list_tab_label(period(datasets_added=1234), added) == "Added · 1,234"


def test_the_pop_up_title_and_caption_name_the_month() -> None:
    month = period(in_progress=True, days_with_data=12)
    assert dialog_title(month) == "2026-11 · Datasets"
    assert dialog_caption(month) == "In progress · 12 days with data"
    assert dialog_caption(period()) == f"Complete · {EMPTY} days with data"


def test_the_detail_frame_sorts_by_publisher_then_dataset_ignoring_case() -> None:
    frame = detail_frame(
        [
            dataset(dataset_url="https://c/", dataset_name="b", publisher_name="beta"),
            dataset(dataset_url="https://b/", dataset_name="Z", publisher_name="Alpha"),
            dataset(dataset_url="https://a/", dataset_name="a", publisher_name="alpha"),
        ]
    )
    assert list(frame.columns) == [col.label for col in DETAIL_COLUMNS]
    assert list(frame["URL"]) == ["https://a/", "https://b/", "https://c/"]


def test_the_detail_frame_trims_names_and_marks_an_unknown_publisher() -> None:
    frame = detail_frame(
        [
            dataset(dataset_name=" Sessions and Facilities", publisher_id="pub_unknown"),
            dataset(dataset_url="https://b/", publisher_name="Known"),
        ]
    )
    assert list(frame["Publisher"]) == ["Known", EMPTY]  # the unknown one sorts last
    assert list(frame["Dataset"]) == [EMPTY, "Sessions and Facilities"]


def test_the_detail_frame_of_nothing_keeps_its_columns() -> None:
    frame = detail_frame([])
    assert frame.empty
    assert list(frame.columns) == [col.label for col in DETAIL_COLUMNS]


def test_the_url_column_links_and_shows_the_host() -> None:
    import re

    url = next(col for col in DETAIL_COLUMNS if col.label == "URL")
    assert url.link_text is not None
    match = re.match(url.link_text, "https://www.willenonice.co.uk/openactive")
    assert match is not None
    assert match.group(1) == "www.willenonice.co.uk"


def test_every_table_figure_explains_itself() -> None:
    for series in SERIES:
        if series.in_table:
            assert series.help, series.field
    columns = {col.label: col for col in TABLE_COLUMNS}
    assert columns["Not ingested"].help == series_for("datasets_not_ingested").help


def test_the_fixture_obeys_the_api_identities(
    periods: tuple[EcosystemGrowthPeriod, ...],
) -> None:
    """With future, without future and not ingested partition the crawled datasets, so the
    table's columns add up the way the API documents."""
    for month in periods:
        parts = (
            month.datasets_with_future,
            month.datasets_without_future,
            month.datasets_not_ingested,
        )
        if None not in parts:
            assert sum(p for p in parts if p is not None) == month.datasets_crawled
