"""DataFrame shaping, RAG tones, KPIs and filtering."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stewards.api.models import HttpFailureDetail, Incident, StallDetail
from stewards.monitors.registry import Col, ColKind, Monitor
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import (
    EMPTY,
    Kpi,
    apply_filters,
    cell_tone,
    csv_bytes,
    filter_options,
    format_cell,
    monitor_kpis,
    parse_detail,
    rag_columns,
    resolve_field,
    search_incidents,
    sort_by_age,
    to_dataframe,
    tone_frame,
)


def make_incident(**overrides: object) -> Incident:
    base: dict[str, object] = {
        "monitor_id": "single_feed_stall",
        "publisher_id": "pub_x",
        "publisher_name": "Publisher X",
        "feed_id": "feed_x",
        "feed_name": "scheduled-sessions",
        "feed_type": "ScheduledSession",
        "feed_url": "https://x.example.org/feed",
        "first_detected": "2026-08-14",
        "days_open": 7,
        "consecutive_days": 7,
        "past_threshold": True,
        "status": "contact_due",
        "trend": [1, 2, 3],
        "detail": {"last_modified": "2026-08-14"},
    }
    return Incident.model_validate(base | overrides)


# --- field resolution -----------------------------------------------------------------


def test_resolve_field_reads_a_plain_attribute(stall_monitor: Monitor) -> None:
    assert resolve_field(stall_monitor, make_incident(), "publisher_name") == "Publisher X"


def test_resolve_field_reads_through_the_detail_model(stall_monitor: Monitor) -> None:
    value = resolve_field(stall_monitor, make_incident(), "detail.last_modified")
    assert value == date(2026, 8, 14)


def test_resolve_field_returns_none_for_a_missing_detail_key(stall_monitor: Monitor) -> None:
    incident = make_incident(detail={})
    assert resolve_field(stall_monitor, incident, "detail.last_modified") is None


def test_resolve_field_returns_none_for_an_unknown_path(stall_monitor: Monitor) -> None:
    assert resolve_field(stall_monitor, make_incident(), "not_a_field") is None


def test_detail_model_ignores_keys_it_does_not_know(stall_monitor: Monitor) -> None:
    incident = make_incident(detail={"last_modified": "2026-08-14", "future_field": 12})
    detail = parse_detail(stall_monitor, incident)
    assert isinstance(detail, StallDetail)
    assert detail.last_modified == date(2026, 8, 14)


def test_detail_model_is_per_monitor(http_monitor: Monitor) -> None:
    incident = make_incident(
        monitor_id="http_failure",
        detail={
            "http_status": "503",
            "error_class": "Service unavailable",
            "last_success": "2026-08-10",
        },
    )
    detail = parse_detail(http_monitor, incident)
    assert isinstance(detail, HttpFailureDetail)
    assert detail.http_status == "503"
    assert detail.last_success == date(2026, 8, 10)


# --- cell formatting ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (ColKind.TEXT, "Freedom Leisure", "Freedom Leisure"),
        (ColKind.TEXT, None, EMPTY),
        (ColKind.TEXT, "", EMPTY),
        (ColKind.MONO, "scheduled-sessions", "scheduled-sessions"),
        (ColKind.NUMBER, 1204, 1204),
        (ColKind.NUMBER, None, None),
        (ColKind.DATE, date(2026, 8, 14), "2026-08-14"),
        (ColKind.DATE, None, EMPTY),
        (ColKind.DAYS, 22, "22d"),
        (ColKind.DAYS, 0, "0d"),
        (ColKind.DAYS, None, EMPTY),
        (ColKind.STATUS, "contact_due", "Contact due"),
        (ColKind.STATUS, None, EMPTY),
        (ColKind.SCORE, 82, 82.0),
        (ColKind.SCORE, None, None),
        (ColKind.SPARKLINE, (1, 2, 3), [1, 2, 3]),
        (ColKind.SPARKLINE, (), []),
        (ColKind.LINK, "https://x/feed", "https://x/feed"),
        (ColKind.LINK, None, None),
    ],
)
def test_format_cell(kind: ColKind, value: object, expected: object) -> None:
    assert format_cell(Col("f", "F", kind), value) == expected


# --- tones ----------------------------------------------------------------------------


def test_cell_tone_is_none_for_non_rag_columns() -> None:
    assert cell_tone(Col("feed_name", "Feed", ColKind.MONO), make_incident(), "x", 7) is None


def test_days_column_tone_uses_the_threshold() -> None:
    col = Col("days_open", "Days", ColKind.DAYS)
    assert cell_tone(col, make_incident(days_open=7), None, 7) is Tone.RED
    assert cell_tone(col, make_incident(days_open=6), None, 7) is Tone.AMBER


def test_status_column_tone_uses_the_status() -> None:
    col = Col("status", "Status", ColKind.STATUS)
    assert cell_tone(col, make_incident(status="new"), None, 7) is Tone.GREY


def test_score_column_tone_uses_the_value() -> None:
    col = Col("quality_score", "Score", ColKind.SCORE)
    assert cell_tone(col, make_incident(), 91, 7) is Tone.GREEN
    assert cell_tone(col, make_incident(), None, 7) is Tone.GREY


# --- frames ---------------------------------------------------------------------------


def test_dataframe_has_the_declared_columns_in_declared_order(stall_monitor: Monitor) -> None:
    frame = to_dataframe(stall_monitor, [make_incident()])
    assert list(frame.columns) == [c.label for c in stall_monitor.columns]
    assert len(frame) == 1


def test_dataframe_values_are_formatted_per_kind(stall_monitor: Monitor) -> None:
    row = to_dataframe(stall_monitor, [make_incident(days_open=22)]).iloc[0]
    assert row["Publisher"] == "Publisher X"
    assert row["Last modified"] == "2026-08-14"
    assert row["Days stalled"] == "22d"
    assert row["Status"] == "Contact due"
    assert row["30d trend"] == [1, 2, 3]


def test_empty_input_gives_an_empty_frame_with_the_declared_columns(
    stall_monitor: Monitor,
) -> None:
    frame = to_dataframe(stall_monitor, [])
    assert frame.empty
    assert list(frame.columns) == [c.label for c in stall_monitor.columns]


def test_frame_row_count_matches_the_sample_payload(stall_monitor: Monitor, stall_page) -> None:
    assert len(to_dataframe(stall_monitor, stall_page.data)) == 23


def test_tone_frame_is_aligned_with_the_data_frame(stall_monitor: Monitor, stall_page) -> None:
    incidents = list(stall_page.data)
    frame = to_dataframe(stall_monitor, incidents)
    tones = tone_frame(stall_monitor, incidents)
    assert frame.shape == tones.shape
    assert list(frame.columns) == list(tones.columns)


def test_tone_frame_only_marks_rag_columns(stall_monitor: Monitor) -> None:
    tones = tone_frame(stall_monitor, [make_incident()])
    assert tones.iloc[0]["Days stalled"] == "red"
    assert tones.iloc[0]["Status"] == "red"
    assert tones.iloc[0]["Publisher"] == ""


def test_tone_frame_is_empty_for_no_incidents(stall_monitor: Monitor) -> None:
    assert tone_frame(stall_monitor, []).empty


def test_rag_columns_lists_only_shaded_labels(
    stall_monitor: Monitor, http_monitor: Monitor
) -> None:
    assert rag_columns(stall_monitor) == ["Days stalled", "Status"]
    assert rag_columns(http_monitor) == ["Consecutive failures", "Status"]


# --- KPIs -----------------------------------------------------------------------------


def test_kpis_count_incidents_publishers_and_past_threshold(stall_monitor: Monitor) -> None:
    incidents = [
        make_incident(days_open=22, past_threshold=True),
        make_incident(publisher_id="pub_x", days_open=3, past_threshold=False),
        make_incident(publisher_id="pub_y", days_open=9, past_threshold=True),
    ]
    count, publishers, past = monitor_kpis(stall_monitor, incidents)
    assert (count.value, publishers.value, past.value) == ("3", "2", "2")
    assert count.label == "feeds stalled"


def test_kpis_on_an_empty_snapshot_read_green(stall_monitor: Monitor) -> None:
    count, publishers, past = monitor_kpis(stall_monitor, [])
    assert (count.value, publishers.value, past.value) == ("0", "0", "0")
    assert count.tone is Tone.GREEN
    assert past.tone is Tone.GREEN
    assert isinstance(count, Kpi)


# --- search and filters ---------------------------------------------------------------


def test_search_is_case_insensitive_across_publisher_feed_and_type() -> None:
    incidents = [
        make_incident(),
        make_incident(publisher_name="Halo Leisure", feed_name="slots"),
    ]
    assert len(search_incidents(incidents, "halo")) == 1
    assert len(search_incidents(incidents, "SLOTS")) == 1
    assert len(search_incidents(incidents, "scheduledsession")) == 2
    assert len(search_incidents(incidents, "no-such-publisher")) == 0


def test_empty_search_returns_everything() -> None:
    incidents = [make_incident(), make_incident()]
    assert len(search_incidents(incidents, "   ")) == 2


def test_search_on_no_incidents_returns_empty() -> None:
    assert search_incidents([], "anything") == []


def test_filter_options_are_sorted_and_exclude_blanks(http_monitor: Monitor, http_page) -> None:
    options = filter_options(http_monitor, http_page.data, "detail.http_status")
    assert options == sorted(options)
    assert "" not in options
    assert "503" in options


def test_filter_options_on_a_missing_field_is_empty(stall_monitor: Monitor) -> None:
    assert filter_options(stall_monitor, [make_incident(feed_type=None)], "feed_type") == []


def test_apply_filters_combines_search_selection_and_threshold(
    http_monitor: Monitor, http_page
) -> None:
    incidents = list(http_page.data)
    only_503 = apply_filters(http_monitor, incidents, selections={"detail.http_status": "503"})
    assert {i.detail["http_status"] for i in only_503} == {"503"}

    past = apply_filters(http_monitor, incidents, past_threshold_only=True)
    assert past
    assert all(i.past_threshold for i in past)

    both = apply_filters(
        http_monitor,
        incidents,
        selections={"detail.http_status": "503"},
        past_threshold_only=True,
    )
    assert all(i.past_threshold and i.detail["http_status"] == "503" for i in both)


def test_apply_filters_ignores_a_blank_selection(http_monitor: Monitor, http_page) -> None:
    incidents = list(http_page.data)
    assert (
        apply_filters(http_monitor, incidents, selections={"detail.http_status": ""})
        == incidents
    )


def test_apply_filters_on_no_incidents_returns_empty(http_monitor: Monitor) -> None:
    assert apply_filters(http_monitor, [], search="x", past_threshold_only=True) == []


def test_threshold_toggle_keeps_the_boundary_row(stall_monitor: Monitor) -> None:
    boundary = make_incident(days_open=7, past_threshold=True)
    assert apply_filters(stall_monitor, [boundary], past_threshold_only=True) == [boundary]


def test_sort_by_age_is_oldest_first_then_alphabetical() -> None:
    incidents = [
        make_incident(days_open=3, publisher_name="B"),
        make_incident(days_open=22, publisher_name="C"),
        make_incident(days_open=22, publisher_name="A"),
    ]
    assert [i.publisher_name for i in sort_by_age(incidents)] == ["A", "C", "B"]


def test_sort_by_age_of_nothing_is_nothing() -> None:
    assert sort_by_age([]) == []


# --- export ---------------------------------------------------------------------------


def test_csv_export_drops_sparkline_columns(stall_monitor: Monitor) -> None:
    frame = to_dataframe(stall_monitor, [make_incident()])
    header = csv_bytes(frame).decode().splitlines()[0]
    assert "Publisher" in header
    assert "30d trend" not in header


def test_csv_export_of_an_empty_frame_is_just_a_header(stall_monitor: Monitor) -> None:
    lines = csv_bytes(to_dataframe(stall_monitor, [])).decode().strip().splitlines()
    assert len(lines) == 1


def test_csv_export_keeps_every_filtered_row(stall_monitor: Monitor, stall_page) -> None:
    shown = apply_filters(stall_monitor, stall_page.data, past_threshold_only=True)
    frame = to_dataframe(stall_monitor, shown)
    body = csv_bytes(frame).decode().strip().splitlines()[1:]
    assert len(body) == len(shown) == 7


def test_csv_export_of_a_frame_without_lists_is_unchanged() -> None:
    frame = pd.DataFrame([{"a": 1, "b": 2}])
    assert csv_bytes(frame).decode().splitlines()[0] == "a,b"
