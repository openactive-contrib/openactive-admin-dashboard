"""The dataset-wide stall monitor: one incident per frozen dataset, not per silent feed.

The payloads mirror the live `/admin/dataset-stall-incidents` shape — dataset fields beside
the shared incident fields, and the dataset's feeds as the breakdown a steward opens. The
API owns the 120-day lookback and the 5-day silence; what is tested here is how the
dashboard reads what it sends.
"""

from __future__ import annotations

from datetime import date

import pytest

from stewards.api.models import DatasetStallDetail, Incident
from stewards.monitors.registry import get_monitor
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import (
    EMPTY,
    apply_filters,
    detail_caption,
    detail_frame,
    detail_items,
    expand,
    monitor_kpis,
    parse_detail,
    sort_rows,
    to_dataframe,
    tone_frame,
)

MONITOR = get_monitor("dataset_stall")
URL = "https://shirleyhighschool.bookteq.com/api/open-active"


def incident(**overrides: object) -> Incident:
    base: dict[str, object] = {
        "monitor_id": "dataset_stall",
        "publisher_id": "pub_shirley-high-school",
        "publisher_name": "Shirley High School",
        "dataset_url": URL,
        "dataset_name": "Shirley High School Facilities",
        "feed_count": 2,
        "first_detected": "2026-09-01",
        "days_open": 9,
        "consecutive_days": 9,
        "past_threshold": True,
        "status": "open",
        "last_contacted": None,
        "trend": [404, None, 0, 0, 0, 0, 0, 0, 0, 0],
        "detail": {
            "last_modified": "2026-09-01",
            "feeds": [
                {
                    "feed_id": "shirleyhighschool-bookteq-com-api-open-active-slots",
                    "feed_name": "slots",
                    "last_published": "2026-09-01",
                    "consecutive_days": 9,
                },
                {
                    "feed_id": "shirleyhighschool-bookteq-com-api-open-active-facility-uses",
                    "feed_name": "facility-uses",
                    "last_published": None,
                    "consecutive_days": None,
                },
            ],
        },
    }
    return Incident.model_validate(base | overrides)


# --- the payload's own shape ---------------------------------------------------------------


def test_the_dataset_fields_arrive_beside_the_shared_ones_and_are_kept() -> None:
    detail = parse_detail(MONITOR, incident())
    assert isinstance(detail, DatasetStallDetail)
    assert detail.dataset_name == "Shirley High School Facilities"
    assert detail.dataset_url == URL
    assert detail.feed_count == 2
    assert detail.last_modified == date(2026, 9, 1)
    assert [feed.feed_name for feed in detail.feeds] == ["slots", "facility-uses"]


def test_this_monitor_does_report_an_age_unlike_the_orphan_one() -> None:
    """A frozen dataset is an ageing fault, so the threshold arithmetic applies to it."""
    row = incident()
    assert row.days_open == 9
    assert row.first_detected == date(2026, 9, 1)
    assert row.consecutive_days == 9


# --- one row per dataset, not per feed -----------------------------------------------------


def test_a_dataset_is_one_row_however_many_feeds_it_has() -> None:
    """The point of the monitor: the feeds failed together, so they are one incident."""
    assert MONITOR.rows is None
    rows = expand(MONITOR, [incident(), incident(publisher_id="pub_conwy", feed_count=7)])
    assert len(rows) == 2
    assert all(row.part is None for row in rows)


def test_the_declared_columns_carry_the_dataset_and_its_silence() -> None:
    row = to_dataframe(MONITOR, expand(MONITOR, [incident()])).iloc[0]
    assert row["Publisher"] == "Shirley High School"
    assert row["Dataset"] == "Shirley High School Facilities"
    assert row["Feeds"] == 2
    assert row["Last published"] == "2026-09-01"
    assert row["Days stalled"] == "9d"
    assert row["Dataset feed"] == URL
    assert row["Recent trend"] == [404.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_the_row_trend_drops_the_snapshots_with_no_ingestion_run() -> None:
    """A day the crawl did not run is not a day of zero publishing."""
    row = to_dataframe(MONITOR, expand(MONITOR, [incident(trend=[9, None, 0])])).iloc[0]
    assert row["Recent trend"] == [9.0, 0.0]
    empty = to_dataframe(MONITOR, expand(MONITOR, [incident(trend=[])])).iloc[0]
    assert empty["Recent trend"] == []


def test_a_dataset_the_batch_could_not_name_renders_as_em_dash() -> None:
    rows = expand(MONITOR, [incident(publisher_name="", detail={"last_modified": None})])
    row = to_dataframe(MONITOR, rows).iloc[0]
    assert row["Publisher"] == EMPTY
    assert row["Last published"] == EMPTY
    # The dataset name folds in from the top level, so it survives an empty detail.
    assert row["Dataset"] == "Shirley High School Facilities"


def test_no_incidents_yields_an_empty_frame_with_the_declared_columns() -> None:
    frame = to_dataframe(MONITOR, expand(MONITOR, []))
    assert frame.empty
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert monitor_kpis(MONITOR, [])[0].value == "0"


def test_the_monitor_declares_no_status_column() -> None:
    """The admin API reports every incident as `open`, so there is no state to show."""
    assert "status" not in [col.field for col in MONITOR.columns]


# --- the threshold boundary ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_open", "past_threshold", "expected"),
    [(4, False, Tone.AMBER), (6, False, Tone.AMBER), (7, True, Tone.RED), (12, True, Tone.RED)],
)
def test_days_stalled_at_the_threshold_is_already_past_it(
    days_open: int, past_threshold: bool, expected: Tone
) -> None:
    assert MONITOR.threshold_days == 7
    rows = expand(MONITOR, [incident(days_open=days_open, past_threshold=past_threshold)])
    assert tone_frame(MONITOR, rows).iloc[0]["Days stalled"] == expected.value
    assert monitor_kpis(MONITOR, rows)[2].value == ("1" if past_threshold else "0")


def test_the_kpis_count_datasets_publishers_and_the_flagged_subset() -> None:
    rows = expand(
        MONITOR,
        [
            incident(),
            incident(publisher_id="pub_conwy", publisher_name="Conwy", past_threshold=False),
            incident(publisher_id="pub_conwy", publisher_name="Conwy", dataset_name="Second"),
        ],
    )
    datasets, publishers, flagged = monitor_kpis(MONITOR, rows)
    assert datasets.label == "datasets frozen"
    assert datasets.value == "3"
    # Two publishers, one of them with two frozen datasets.
    assert publishers.value == "2"
    assert flagged.value == "2"


def test_rows_are_ordered_longest_stalled_first() -> None:
    rows = expand(
        MONITOR,
        [
            incident(publisher_name="Fresh", days_open=5),
            incident(publisher_name="Oldest", days_open=30),
            incident(publisher_name="Middle", days_open=9),
        ],
    )
    assert [r.incident.publisher_name for r in sort_rows(MONITOR, rows)] == [
        "Oldest",
        "Middle",
        "Fresh",
    ]


def test_the_search_matches_the_dataset_name_as_well_as_the_publisher() -> None:
    rows = expand(MONITOR, [incident()])
    assert len(apply_filters(MONITOR, rows, search="shirley high school facilities")) == 1
    assert len(apply_filters(MONITOR, rows, search="Shirley")) == 1
    assert apply_filters(MONITOR, rows, search="no-such-dataset") == []


def test_the_threshold_toggle_narrows_to_the_flagged_datasets() -> None:
    rows = expand(MONITOR, [incident(), incident(publisher_id="pub_c", past_threshold=False)])
    assert len(apply_filters(MONITOR, rows, past_threshold_only=True)) == 1


# --- the frozen-feeds table a selected row opens ------------------------------------------


def test_the_row_detail_lists_every_feed_and_when_each_last_published() -> None:
    spec = MONITOR.row_detail
    assert spec is not None
    rows = detail_items(MONITOR, incident(), spec)
    frame = detail_frame(MONITOR, rows, spec)
    assert list(frame.columns) == ["Feed", "Last published", "Days silent", "Feed id"]
    assert list(frame["Feed"]) == ["slots", "facility-uses"]
    assert list(frame["Last published"]) == ["2026-09-01", EMPTY]
    assert list(frame["Days silent"]) == ["9d", EMPTY]


def test_a_dataset_with_no_feed_breakdown_has_no_row_detail_to_show() -> None:
    spec = MONITOR.row_detail
    assert spec is not None
    assert detail_items(MONITOR, incident(detail={"feeds": []}), spec) == []
    assert detail_items(MONITOR, incident(detail={}), spec) == []


def test_the_row_detail_caption_needs_no_count_for_this_monitor() -> None:
    """Unlike the orphan monitor, the API sends every feed, not just the worst few."""
    spec = MONITOR.row_detail
    assert spec is not None
    assert spec.count_field is None
    assert detail_caption(MONITOR, incident(), spec) == spec.caption
    assert "{count}" not in detail_caption(MONITOR, incident(), spec)
