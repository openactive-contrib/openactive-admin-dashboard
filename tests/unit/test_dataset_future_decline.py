"""The future opportunity decline monitor: its feed rows, its window arithmetic, its order.

The payloads here are the shape the live `/admin/dataset-future-decline-incidents` endpoint
returns: the dataset's identity beside the shared incident fields rather than nested under
`detail`, the window's totals in `detail`, and one entry per feed in `detail.feeds`.
"""

from __future__ import annotations

import pytest

from stewards.api.models import FutureDeclineDetail, Incident
from stewards.monitors.registry import get_monitor
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import (
    EMPTY,
    apply_filters,
    expand,
    filter_options,
    monitor_kpis,
    parse_detail,
    snapshot_total,
    sort_rows,
    to_dataframe,
    tone_frame,
)

MONITOR = get_monitor("dataset_future_decline")

URL = "https://better-admin.org.uk/api/openactive/better"


def feed(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "feed_id": "better-admin-scheduled-sessions",
        "feed_name": "scheduled-sessions",
        "reason": "monotonic_decline",
        "start_future": 190167,
        "current_future": 177186,
        "drop": 12981,
        "drop_percent": 6.83,
        "consecutive_declining_days": 4,
        "largest_daily_drop_percent": 2.01,
        "updated_in_window": 332,
        "deletes_in_window": 16131,
    }
    return base | overrides


def incident(**overrides: object) -> Incident:
    base: dict[str, object] = {
        "monitor_id": "dataset_future_decline",
        "publisher_id": "pub_better-better-admin",
        "publisher_name": "Better (better-admin)",
        "dataset_url": URL,
        "dataset_name": "Better Sessions and Facilities",
        "feed_count": 1,
        "first_detected": "2026-08-12",
        "days_open": 9,
        "consecutive_days": 9,
        "past_threshold": True,
        "status": "open",
        "last_contacted": None,
        "trend": [190167, 187137, 183814, 180114, 177186],
        "detail": {
            "reason": "monotonic_decline",
            "window_days": 5,
            "start_total": 190167,
            "current_total": 177186,
            "drop": 12981,
            "drop_percent": 6.83,
            "feeds": [feed()],
        },
    }
    return Incident.model_validate(base | overrides)


# --- the payload's own shape ---------------------------------------------------------------


def test_the_dataset_identity_arrives_beside_the_shared_fields_and_is_kept() -> None:
    """`extra="ignore"` would drop these, so the model folds them into `detail`."""
    detail = parse_detail(MONITOR, incident())
    assert isinstance(detail, FutureDeclineDetail)
    assert detail.dataset_name == "Better Sessions and Facilities"
    assert detail.dataset_url == URL
    assert detail.feed_count == 1
    assert detail.window_days == 5
    assert detail.start_total == 190167
    assert [f.feed_name for f in detail.feeds] == ["scheduled-sessions"]


def test_the_dataset_reports_its_own_totals_under_the_names_its_feeds_use() -> None:
    """So a dataset with no breakdown fills the same columns instead of leaving them blank."""
    detail = parse_detail(MONITOR, incident())
    assert detail.start_future == detail.start_total
    assert detail.current_future == detail.current_total


# --- the window arithmetic the page exists for --------------------------------------------


@pytest.mark.parametrize(
    ("updated", "deletes", "expected"),
    [
        (332, 16131, -15799),  # the finding: far more removed than refreshed
        (44773, 821, 43952),  # a busy feed whose decline is not a deletion story
        (0, 0, 0),  # the boundary: a quiet window is balanced, not absent
        (5, 5, 0),
        (None, 12, None),  # an unreported count is not a balanced one
        (12, None, None),
        (None, None, None),
    ],
)
def test_the_delta_is_what_the_window_updated_minus_what_it_deleted(
    updated: int | None, deletes: int | None, expected: int | None
) -> None:
    one = feed(updated_in_window=updated, deletes_in_window=deletes)
    rows = expand(MONITOR, [incident(detail={"feeds": [one]})])
    assert rows[0].part is not None
    assert rows[0].part.delta_in_window == expected
    assert to_dataframe(MONITOR, rows).iloc[0]["Delta"] == expected


def test_the_deficit_is_the_delta_the_other_way_up() -> None:
    """It is what the table sorts on, so that the hardest-deleting feed is the top row."""
    rows = expand(MONITOR, [incident()])
    assert rows[0].part is not None
    assert rows[0].part.delta_in_window == -15799
    assert rows[0].part.deficit_in_window == 15799


def test_a_feed_with_no_item_counts_has_no_deficit_either() -> None:
    rows = expand(MONITOR, [incident(detail={"feeds": [feed(updated_in_window=None)]})])
    assert rows[0].part is not None
    assert rows[0].part.deficit_in_window is None


# --- one row per feed ----------------------------------------------------------------------


def test_each_dataset_becomes_one_row_per_feed() -> None:
    two_feeds = incident(
        publisher_id="pub_bookwhen",
        publisher_name="Bookwhen",
        dataset_name="Bookwhen Courses, Sessions, and Events",
        feed_count=2,
        detail={
            "reason": "both",
            "start_total": 166,
            "current_total": 137,
            "drop": 29,
            "drop_percent": 17.47,
            "feeds": [
                feed(feed_name="sessionseries", updated_in_window=59, deletes_in_window=0),
                feed(feed_name="courseinstances", updated_in_window=16, deletes_in_window=0),
            ],
        },
    )
    frame = to_dataframe(MONITOR, expand(MONITOR, [two_feeds]))
    assert list(frame["Feed"]) == ["sessionseries", "courseinstances"]
    assert list(frame["Updated"]) == [59, 16]
    assert list(frame["Delta"]) == [59, 16]
    assert set(frame["Dataset"]) == {"Bookwhen Courses, Sessions, and Events"}


def test_the_declared_columns_land_in_their_declared_order() -> None:
    frame = to_dataframe(MONITOR, expand(MONITOR, [incident()]))
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    row = frame.iloc[0]
    assert row["Publisher"] == "Better (better-admin)"
    assert row["Dataset"] == "Better Sessions and Facilities"
    assert row["Feed"] == "scheduled-sessions"
    assert row["Reason"] == "Monotonic decline"
    assert row["Future now"] == 177186
    assert row["Drop"] == pytest.approx(6.83)
    assert row["Updated"] == 332
    assert row["Deletes"] == 16131
    assert row["Delta"] == -15799
    assert row["Days declining"] == "9d"
    assert row["Recent trend"] == [190167.0, 187137.0, 183814.0, 180114.0, 177186.0]
    assert row["Dataset feed"] == URL


def test_a_snapshot_the_batch_had_no_figure_for_is_dropped_from_the_row_sparkline() -> None:
    rows = expand(MONITOR, [incident(trend=[3612, None, 3588, None, 3522])])
    assert to_dataframe(MONITOR, rows).iloc[0]["Recent trend"] == [3612.0, 3588.0, 3522.0]


def test_a_dataset_flagged_without_a_feed_breakdown_falls_back_to_its_own_totals() -> None:
    """Dropping the row would hide the finding; blanking it would hide the decline."""
    rows = expand(
        MONITOR,
        [
            incident(
                detail={
                    "reason": "sharp_drop",
                    "start_total": 143,
                    "current_total": 121,
                    "drop": 22,
                    "drop_percent": 15.38,
                }
            )
        ],
    )
    assert len(rows) == 1
    row = to_dataframe(MONITOR, rows).iloc[0]
    assert row["Feed"] == EMPTY
    assert row["Reason"] == "Sharp drop"
    assert row["Future now"] == 121
    assert row["Drop"] == pytest.approx(15.38)
    # The batch reports item counts per feed only, so a dataset-level row has none to show.
    assert row["Updated"] is None
    assert row["Deletes"] is None
    assert row["Delta"] is None


def test_an_unrecognised_reason_still_reads_rather_than_showing_a_raw_token() -> None:
    rows = expand(MONITOR, [incident(detail={"feeds": [feed(reason="expiry_wave")]})])
    assert to_dataframe(MONITOR, rows).iloc[0]["Reason"] == "Expiry wave"


def test_a_feed_the_batch_gave_no_reason_reads_empty() -> None:
    rows = expand(MONITOR, [incident(detail={"feeds": [feed(reason=None)]})])
    assert to_dataframe(MONITOR, rows).iloc[0]["Reason"] == EMPTY


def test_no_incidents_yields_an_empty_frame_with_the_declared_columns() -> None:
    frame = to_dataframe(MONITOR, expand(MONITOR, []))
    assert frame.empty
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert monitor_kpis(MONITOR, [])[0].value == "0"


# --- the contact threshold, at its boundary -----------------------------------------------


@pytest.mark.parametrize(
    ("days", "expected"),
    [(3, Tone.GREY), (4, Tone.AMBER), (6, Tone.AMBER), (7, Tone.RED), (9, Tone.RED)],
)
def test_the_day_count_shades_red_from_the_threshold_inclusive(
    days: int, expected: Tone
) -> None:
    assert MONITOR.threshold_days == 7
    rows = expand(MONITOR, [incident(days_open=days)])
    assert to_dataframe(MONITOR, rows).iloc[0]["Days declining"] == f"{days}d"
    assert tone_frame(MONITOR, rows).iloc[0]["Days declining"] == expected.value


def test_the_threshold_toggle_reads_the_flag_the_api_sets_not_the_day_count() -> None:
    rows = expand(
        MONITOR,
        [incident(), incident(publisher_id="pub_our-parks", days_open=4, past_threshold=False)],
    )
    assert len(rows) == 2
    assert len(apply_filters(MONITOR, rows, past_threshold_only=True)) == 1


# --- the drop share -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(0.0, Tone.GREEN), (19.9, Tone.GREEN), (20.0, Tone.AMBER), (50.0, Tone.RED)],
)
def test_the_drop_share_shades_the_other_way_from_a_quality_score(
    percent: float, expected: Tone
) -> None:
    """High is bad here: on the score scale a dataset losing everything would read green."""
    rows = expand(MONITOR, [incident(detail={"feeds": [feed(drop_percent=percent)]})])
    assert tone_frame(MONITOR, rows).iloc[0]["Drop"] == expected.value


def test_a_feed_the_batch_reported_no_share_for_carries_no_tone() -> None:
    rows = expand(MONITOR, [incident(detail={"feeds": [feed(drop_percent=None)]})])
    assert to_dataframe(MONITOR, rows).iloc[0]["Drop"] is None
    assert tone_frame(MONITOR, rows).iloc[0]["Drop"] == Tone.GREY.value


# --- ordering, KPIs and filters -----------------------------------------------------------


def test_rows_are_ordered_by_the_delta_worst_first() -> None:
    """Worst is the most negative delta: the feed deleting hardest against what it updated."""
    rows = expand(
        MONITOR,
        [
            incident(
                detail={
                    "feeds": [
                        feed(feed_name="busy", updated_in_window=44773, deletes_in_window=821)
                    ]
                }
            ),
            incident(
                publisher_id="pub_our-parks",
                publisher_name="Our Parks",
                detail={
                    "feeds": [
                        feed(feed_name="events", updated_in_window=3, deletes_in_window=5)
                    ]
                },
            ),
            incident(
                publisher_id="pub_better-better-admin",
                detail={"feeds": [feed(feed_name="scheduled-sessions")]},
            ),
        ],
    )
    ordered = sort_rows(MONITOR, rows)
    assert [row.part.feed_name for row in ordered if row.part] == [
        "scheduled-sessions",  # -15,799
        "events",  # -2
        "busy",  # +43,952
    ]


def test_a_row_with_no_delta_does_not_take_the_top_of_the_table() -> None:
    rows = expand(
        MONITOR,
        [
            incident(detail={"feeds": [feed(feed_name="unreported", updated_in_window=None)]}),
            incident(
                publisher_id="pub_better-better-admin",
                detail={"feeds": [feed(feed_name="scheduled-sessions")]},
            ),
        ],
    )
    ordered = sort_rows(MONITOR, rows)
    assert [row.part.feed_name for row in ordered if row.part] == [
        "scheduled-sessions",
        "unreported",
    ]


def test_the_kpis_count_datasets_once_however_many_feeds_they_explode_into() -> None:
    rows = expand(
        MONITOR,
        [
            incident(
                detail={
                    "feeds": [
                        feed(feed_name="sessionseries"),
                        feed(feed_name="courseinstances"),
                    ]
                }
            ),
            incident(
                publisher_id="pub_our-parks",
                publisher_name="Our Parks",
                past_threshold=False,
                detail={"feeds": [feed(feed_name="events")]},
            ),
        ],
    )
    assert len(rows) == 3
    datasets, publishers, past = monitor_kpis(MONITOR, rows)
    assert datasets.label == "datasets declining"
    assert datasets.value == "2"
    assert publishers.value == "2"
    assert past.value == "1"


def test_the_snapshot_total_is_the_dataset_count_the_summary_reports() -> None:
    rows = expand(MONITOR, [incident(), incident(publisher_id="pub_gll")])
    assert snapshot_total(MONITOR, rows) == 2


def test_the_reason_filter_reads_the_feed_rather_than_the_dataset() -> None:
    rows = expand(
        MONITOR,
        [
            incident(
                detail={
                    "reason": "both",
                    "feeds": [
                        feed(feed_name="sessionseries", reason="sharp_drop"),
                        feed(feed_name="courseinstances", reason="monotonic_decline"),
                    ],
                }
            )
        ],
    )
    assert filter_options(MONITOR, rows, "part.reason_label") == [
        "Monotonic decline",
        "Sharp drop",
    ]
    sharp = apply_filters(MONITOR, rows, selections={"part.reason_label": "Sharp drop"})
    assert [row.part.feed_name for row in sharp if row.part] == ["sessionseries"]


def test_the_search_matches_the_dataset_name_as_well_as_the_publisher() -> None:
    rows = expand(MONITOR, [incident()])
    assert len(apply_filters(MONITOR, rows, search="better sessions")) == 1
    assert len(apply_filters(MONITOR, rows, search="Better (better-admin)")) == 1
    assert apply_filters(MONITOR, rows, search="no-such-dataset") == []
