"""The orphaned children monitor: its breakdown rows, its shares, and its missing age.

The payloads here are the shape the live `/admin/dataset-orphaned-children-incidents`
endpoint returns — measurements beside the shared incident fields rather than nested under
`detail`, and no `first_detected` or `days_open` at all, because this monitor counts a
snapshot rather than tracking an ageing fault.
"""

from __future__ import annotations

import pytest

from stewards.api.models import Incident, OrphanedChildrenDetail
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

MONITOR = get_monitor("dataset_orphaned_children")

URL = "https://openactive.played.co/openactive/"
PARENT = "https://openactive.played.co/api/identifiers/facility-uses/df9ac607"


def incident(**overrides: object) -> Incident:
    base: dict[str, object] = {
        "monitor_id": "dataset_orphaned_children",
        "publisher_id": "pub_played",
        "publisher_name": "Played",
        "dataset_url": URL,
        "dataset_name": "Played Sessions and Facilities",
        "child_count": 230660,
        "checked_count": 230660,
        "orphan_count": 91748,
        "orphan_share": 0.39776294112546606,
        "missing_parent_count": 862,
        "past_threshold": True,
        "status": "open",
        "last_contacted": None,
        "detail": {
            "by_kind": [
                {
                    "kind": "Slot",
                    "child_count": 198790,
                    "checked_count": 198790,
                    "orphan_count": 79744,
                    "missing_parent_count": 77,
                },
                {
                    "kind": "ScheduledSession",
                    "child_count": 31870,
                    "checked_count": 31870,
                    "orphan_count": 12004,
                    "missing_parent_count": 785,
                },
            ],
            "missing_parents": [{"missing_id": PARENT, "child_count": 12213}],
        },
    }
    return Incident.model_validate(base | overrides)


# --- the payload's own shape ---------------------------------------------------------------


def test_the_measurements_arrive_beside_the_shared_fields_and_are_kept() -> None:
    """`extra="ignore"` would drop these, so the model folds them into `detail`."""
    detail = parse_detail(MONITOR, incident())
    assert isinstance(detail, OrphanedChildrenDetail)
    assert detail.dataset_name == "Played Sessions and Facilities"
    assert detail.dataset_url == URL
    assert detail.orphan_count == 91748
    assert detail.missing_parent_count == 862
    assert [kind.kind for kind in detail.by_kind] == ["Slot", "ScheduledSession"]
    assert detail.missing_parents[0].missing_id == PARENT


def test_this_monitor_reports_no_incident_age() -> None:
    row = incident()
    assert row.days_open is None
    assert row.first_detected is None
    assert row.past_threshold is True


def test_a_nested_detail_key_wins_over_the_top_level_one() -> None:
    """The explicit value is the nested one; folding must not overwrite it."""
    row = incident(orphan_count=1, detail={"orphan_count": 99})
    assert parse_detail(MONITOR, row).orphan_count == 99


# --- one row per dataset x child type -----------------------------------------------------


def test_each_dataset_becomes_one_row_per_child_type() -> None:
    rows = expand(MONITOR, [incident()])
    assert len(rows) == 2
    assert [row.incident for row in rows] == [rows[0].incident, rows[0].incident]
    frame = to_dataframe(MONITOR, rows)
    assert list(frame["Child type"]) == ["Slot", "ScheduledSession"]
    assert list(frame["Orphans"]) == [79744, 12004]
    assert list(frame["Missing parents"]) == [77, 785]
    assert set(frame["Publisher"]) == {"Played"}


def test_the_breakdown_columns_land_in_their_declared_columns() -> None:
    row = to_dataframe(MONITOR, expand(MONITOR, [incident()])).iloc[0]
    assert row["Dataset"] == "Played Sessions and Facilities"
    assert row["Children checked"] == 198790
    assert row["Dataset feed"] == URL
    assert row["Share orphaned"] == pytest.approx(100 * 79744 / 198790)


def test_a_dataset_the_batch_reported_without_a_breakdown_falls_back_to_its_own_figures() -> (
    None
):
    """Dropping the row would hide the finding, and blanking its cells would hide the count.

    The dataset has a figure of its own beside the breakdown, so the one row it produces
    reports that instead. Only the child type is genuinely unknown.
    """
    rows = expand(MONITOR, [incident(detail={})])
    assert len(rows) == 1
    row = to_dataframe(MONITOR, rows).iloc[0]
    assert row["Child type"] == EMPTY
    assert row["Orphans"] == 91748
    assert row["Children checked"] == 230660
    assert row["Missing parents"] == 862
    assert row["Share orphaned"] == pytest.approx(39.776294112546606)
    assert row["Publisher"] == "Played"


def test_a_dataset_with_neither_a_breakdown_nor_a_figure_reads_empty() -> None:
    rows = expand(MONITOR, [incident(detail={}, orphan_count=None, checked_count=None)])
    row = to_dataframe(MONITOR, rows).iloc[0]
    assert row["Orphans"] is None
    assert row["Children checked"] is None


def test_the_total_counts_a_dataset_with_no_breakdown_exactly_once() -> None:
    """The regression: summing only the breakdown silently dropped such a dataset's orphans."""
    rows = expand(
        MONITOR, [incident(), incident(publisher_id="pub_conwy", detail={}, orphan_count=2)]
    )
    assert len(rows) == 3
    assert snapshot_total(MONITOR, rows) == 79744 + 12004 + 2


def test_a_publisher_the_batch_could_not_name_renders_as_em_dash() -> None:
    rows = expand(MONITOR, [incident(publisher_id="pub_unknown", publisher_name="")])
    assert to_dataframe(MONITOR, rows).iloc[0]["Publisher"] == EMPTY


def test_no_incidents_yields_an_empty_frame_with_the_declared_columns() -> None:
    frame = to_dataframe(MONITOR, expand(MONITOR, []))
    assert frame.empty
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert monitor_kpis(MONITOR, [])[0].value == "0"


# --- the share, and the boundary the shading turns on -------------------------------------


@pytest.mark.parametrize(
    ("orphans", "checked", "expected_share", "expected_tone"),
    [
        (0, 100, 0.0, Tone.GREEN),
        (19, 100, 19.0, Tone.GREEN),
        (20, 100, 20.0, Tone.AMBER),
        (49, 100, 49.0, Tone.AMBER),
        (50, 100, 50.0, Tone.RED),
        (100, 100, 100.0, Tone.RED),
    ],
)
def test_the_orphan_share_shades_the_other_way_from_a_quality_score(
    orphans: int, checked: int, expected_share: float, expected_tone: Tone
) -> None:
    """High is bad here: on the score scale a wholly orphaned dataset would read green."""
    detail = {"by_kind": [{"kind": "Slot", "orphan_count": orphans, "checked_count": checked}]}
    rows = expand(MONITOR, [incident(detail=detail)])
    assert to_dataframe(MONITOR, rows).iloc[0]["Share orphaned"] == pytest.approx(
        expected_share
    )
    assert tone_frame(MONITOR, rows).iloc[0]["Share orphaned"] == expected_tone.value


def test_a_child_type_the_crawl_did_not_reach_reports_no_share() -> None:
    """Zero checked is not zero orphaned, so the share is absent rather than 0%."""
    detail = {
        "by_kind": [{"kind": "ScheduledSession", "orphan_count": 0, "checked_count": 0}],
    }
    rows = expand(MONITOR, [incident(detail=detail)])
    assert to_dataframe(MONITOR, rows).iloc[0]["Share orphaned"] is None
    assert tone_frame(MONITOR, rows).iloc[0]["Share orphaned"] == Tone.GREY.value


# --- KPIs, ordering and filters -----------------------------------------------------------


def test_the_headline_kpi_sums_the_orphans_rather_than_counting_rows() -> None:
    rows = expand(
        MONITOR,
        [incident(), incident(publisher_id="pub_conwy", detail={}, orphan_count=2)],
    )
    orphans, publishers, flagged = monitor_kpis(MONITOR, rows)
    assert orphans.label == "orphaned children"
    assert orphans.value == "91,750"
    # Three rows, two datasets: neither publishers nor the flagged count double-counts.
    assert len(rows) == 3
    assert publishers.value == "2"
    assert flagged.value == "2"


def test_the_snapshot_total_is_the_figure_the_summary_reports() -> None:
    assert snapshot_total(MONITOR, expand(MONITOR, [incident()])) == 79744 + 12004


def test_rows_are_ordered_by_orphans_not_by_age() -> None:
    """Every incident here has the same (absent) age, so the volume has to do the ordering."""
    rows = sort_rows(MONITOR, expand(MONITOR, [incident()]))
    assert [row.part.kind for row in rows if row.part] == ["Slot", "ScheduledSession"]
    assert [row.part.orphan_count for row in rows if row.part] == [79744, 12004]


def test_the_child_type_filter_reads_the_breakdown() -> None:
    rows = expand(MONITOR, [incident()])
    assert filter_options(MONITOR, rows, "part.kind") == ["ScheduledSession", "Slot"]
    slots = apply_filters(MONITOR, rows, selections={"part.kind": "Slot"})
    assert [row.part.kind for row in slots if row.part] == ["Slot"]


def test_the_search_matches_the_dataset_name_as_well_as_the_publisher() -> None:
    rows = expand(MONITOR, [incident()])
    assert len(apply_filters(MONITOR, rows, search="played sessions")) == 2
    assert len(apply_filters(MONITOR, rows, search="Played")) == 2
    assert apply_filters(MONITOR, rows, search="no-such-dataset") == []


def test_the_threshold_toggle_still_reads_the_flag_the_api_sets() -> None:
    rows = expand(
        MONITOR, [incident(), incident(publisher_id="pub_conwy", past_threshold=False)]
    )
    assert len(apply_filters(MONITOR, rows, past_threshold_only=True)) == 2
    assert len(rows) == 4
