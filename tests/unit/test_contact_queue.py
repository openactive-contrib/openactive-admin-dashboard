"""The cross-monitor contact queue union."""

from __future__ import annotations

from stewards.api.models import Incident, IncidentPage
from stewards.monitors import contact_queue
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import EMPTY


def incident(monitor_id: str, publisher: str, days: int, **extra: object) -> Incident:
    return Incident.model_validate(
        {
            "monitor_id": monitor_id,
            "publisher_id": f"pub_{publisher.lower()}",
            "publisher_name": publisher,
            "feed_name": "scheduled-sessions",
            "feed_type": "ScheduledSession",
            "first_detected": "2026-08-01",
            "days_open": days,
            "past_threshold": days >= 7,
            "status": "contact_due",
            **extra,
        }
    )


def test_columns_are_fixed_and_the_frame_matches_them(contact_queue_page: IncidentPage) -> None:
    frame = contact_queue.to_dataframe(contact_queue_page.data)
    assert list(frame.columns) == list(contact_queue.COLUMNS)
    assert len(frame) == 10


def test_queue_is_oldest_first(contact_queue_page: IncidentPage) -> None:
    frame = contact_queue.to_dataframe(contact_queue_page.data)
    ages = [int(value.removesuffix("d")) for value in frame["Days open"]]
    assert ages == sorted(ages, reverse=True)


def test_one_publisher_failing_two_monitors_appears_once_per_monitor() -> None:
    rows = [
        incident("single_feed_stall", "Halo Leisure", 12),
        incident("http_failure", "Halo Leisure", 9),
    ]
    frame = contact_queue.to_dataframe(rows)
    assert list(frame["Publisher"]) == ["Halo Leisure", "Halo Leisure"]
    assert list(frame["Monitor"]) == ["Single-feed stalls", "HTTP endpoint failures"]
    assert list(frame["Days open"]) == ["12d", "9d"]


def test_monitor_column_uses_the_registry_name() -> None:
    frame = contact_queue.to_dataframe([incident("http_failure", "Halo Leisure", 9)])
    assert frame.iloc[0]["Monitor"] == "HTTP endpoint failures"


def test_detail_column_uses_the_monitor_summary_field() -> None:
    frame = contact_queue.to_dataframe([incident("single_feed_stall", "Halo Leisure", 9)])
    assert frame.iloc[0]["Detail"] == "scheduled-sessions"


def test_missing_detail_renders_as_em_dash() -> None:
    frame = contact_queue.to_dataframe([incident("single_feed_stall", "X", 9, feed_name=None)])
    assert frame.iloc[0]["Detail"] == EMPTY


def test_unknown_last_contacted_renders_as_em_dash() -> None:
    frame = contact_queue.to_dataframe([incident("single_feed_stall", "X", 9)])
    assert frame.iloc[0]["Last contacted"] == EMPTY


def test_known_last_contacted_renders_iso() -> None:
    frame = contact_queue.to_dataframe(
        [incident("single_feed_stall", "X", 9, last_contacted="2026-08-12")]
    )
    assert frame.iloc[0]["Last contacted"] == "2026-08-12"


def test_rows_from_unregistered_monitors_are_dropped_and_reported() -> None:
    rows = [
        incident("single_feed_stall", "Halo Leisure", 12),
        incident("orphan_children", "Better (GLL)", 16),
    ]
    assert len(contact_queue.known_incidents(rows)) == 1
    assert contact_queue.unknown_monitor_ids(rows) == ["orphan_children"]
    assert len(contact_queue.to_dataframe(rows)) == 1


def test_no_unknown_monitors_in_the_sample_queue(contact_queue_page: IncidentPage) -> None:
    assert contact_queue.unknown_monitor_ids(contact_queue_page.data) == []


def test_empty_queue_gives_an_empty_frame_with_the_columns() -> None:
    frame = contact_queue.to_dataframe([])
    assert frame.empty
    assert list(frame.columns) == list(contact_queue.COLUMNS)
    assert contact_queue.tone_frame([]).empty


def test_tone_frame_shades_only_the_rag_columns() -> None:
    tones = contact_queue.tone_frame([incident("single_feed_stall", "X", 7)])
    assert tones.iloc[0]["Days open"] == Tone.RED.value
    assert tones.iloc[0]["Status"] == Tone.RED.value
    assert tones.iloc[0]["Publisher"] == ""


def test_tone_frame_boundary_row_is_red() -> None:
    tones = contact_queue.tone_frame([incident("single_feed_stall", "X", 7)])
    assert tones.iloc[0]["Days open"] == "red"


def test_tone_frame_is_aligned_with_the_data_frame(contact_queue_page: IncidentPage) -> None:
    rows = contact_queue_page.data
    assert contact_queue.to_dataframe(rows).shape == contact_queue.tone_frame(rows).shape


def test_kpis_count_rows_publishers_and_contacted(contact_queue_page: IncidentPage) -> None:
    (total, publishers, contacted) = contact_queue.queue_kpis(contact_queue_page.data)
    assert total[1] == "10"
    assert int(publishers[1]) <= 10
    assert contacted[0] == "already contacted"


def test_kpis_of_an_empty_queue_read_green() -> None:
    total, publishers, contacted = contact_queue.queue_kpis([])
    assert (total[1], publishers[1], contacted[1]) == ("0", "0", "0")
    assert total[2] is Tone.GREEN
    assert contacted[2] is Tone.GREY


def test_kpis_ignore_unregistered_monitors() -> None:
    rows = [incident("orphan_children", "Better (GLL)", 16)]
    assert contact_queue.queue_kpis(rows)[0][1] == "0"


def test_the_column_labels_are_derived_from_the_specs() -> None:
    assert tuple(c.label for c in contact_queue.COLUMN_SPECS) == contact_queue.COLUMNS


def test_the_rag_columns_are_derived_from_the_spec_kinds() -> None:
    assert contact_queue.RAG_COLUMNS == ("Days open", "Status")
