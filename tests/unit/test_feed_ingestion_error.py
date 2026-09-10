"""The feed ingestion error monitor: its detail fields, table cells and threshold edge.

The payloads here are the shape the live `/admin/feed-ingestion-error-incidents` endpoint
returns, including the parse-error code that is not an HTTP status.
"""

from __future__ import annotations

from datetime import date

import pytest

from stewards.api.models import FeedIngestionErrorDetail, Incident
from stewards.monitors.registry import get_monitor
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import (
    EMPTY,
    apply_filters,
    filter_options,
    monitor_kpis,
    parse_detail,
    to_dataframe,
    tone_frame,
)

MONITOR = get_monitor("feed_ingestion_error")

URL = "https://ourparks.org.uk/api/events"
MESSAGE = f"RPDE page has no 'items' key at {URL}?afterTimestamp=1788850837&afterId=108995"


def incident(**overrides: object) -> Incident:
    base: dict[str, object] = {
        "monitor_id": "feed_ingestion_error",
        "publisher_id": "pub_our-parks",
        "publisher_name": "Our Parks",
        "feed_id": "ourparks-org-uk-api-events",
        "feed_name": "events",
        "feed_type": "Event",
        "feed_url": URL,
        "first_detected": "2026-09-02",
        "days_open": 8,
        "consecutive_days": 8,
        "past_threshold": True,
        "status": "open",
        "trend": [0, 0, 0, 1, 1, 1, 1, 1, 1, 1],
        "detail": {
            "error_code": "MISSING_ITEMS",
            "error_message": MESSAGE,
            "last_completed": "2026-09-01",
        },
        "quality_score": None,
    }
    return Incident.model_validate(base | overrides)


def test_detail_parses_the_live_shape() -> None:
    detail = parse_detail(MONITOR, incident())
    assert isinstance(detail, FeedIngestionErrorDetail)
    assert detail.error_code == "MISSING_ITEMS"
    assert detail.error_message == MESSAGE
    assert detail.last_completed == date(2026, 9, 1)


def test_the_error_code_and_message_land_in_their_own_columns() -> None:
    row = to_dataframe(MONITOR, [incident()]).iloc[0]
    assert row["Error code"] == "MISSING_ITEMS"
    assert row["Error message"] == MESSAGE
    assert row["Last completed"] == "2026-09-01"
    assert row["Consecutive failures"] == "8d"


def test_the_row_trend_renders_as_a_sparkline_series() -> None:
    row = to_dataframe(MONITOR, [incident()]).iloc[0]
    assert row["Recent trend"] == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]


def test_a_row_trend_with_gaps_drops_the_missing_snapshots() -> None:
    row = to_dataframe(MONITOR, [incident(trend=[1, None, 2])]).iloc[0]
    assert row["Recent trend"] == [1.0, 2.0]
    assert to_dataframe(MONITOR, [incident(trend=[])]).iloc[0]["Recent trend"] == []


def test_a_detail_field_the_api_omits_renders_as_em_dash() -> None:
    row = to_dataframe(MONITOR, [incident(detail={"error_code": "500"})]).iloc[0]
    assert row["Error code"] == "500"
    assert row["Error message"] == EMPTY
    assert row["Last completed"] == EMPTY


def test_neither_detail_column_carries_a_rag_tone() -> None:
    tones = tone_frame(MONITOR, [incident()]).iloc[0]
    assert tones["Error code"] == ""
    assert tones["Error message"] == ""
    assert tones["Consecutive failures"] == Tone.RED.value


def test_no_incidents_yields_an_empty_frame_with_the_declared_columns() -> None:
    frame = to_dataframe(MONITOR, [])
    assert frame.empty
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert monitor_kpis(MONITOR, [])[0].value == "0"


@pytest.mark.parametrize(
    ("days_open", "past_threshold", "expected"),
    [(6, False, Tone.AMBER), (7, True, Tone.RED), (8, True, Tone.RED)],
)
def test_days_open_at_the_threshold_is_already_past_it(
    days_open: int, past_threshold: bool, expected: Tone
) -> None:
    assert MONITOR.threshold_days == 7
    rows = [incident(days_open=days_open, past_threshold=past_threshold)]
    assert tone_frame(MONITOR, rows).iloc[0]["Consecutive failures"] == expected.value
    assert monitor_kpis(MONITOR, rows)[2].value == ("1" if past_threshold else "0")


def test_the_error_code_filter_reads_the_detail_model() -> None:
    rows = [
        incident(),
        incident(publisher_id="pub_everyone-active", detail={"error_code": "404"}),
        incident(publisher_id="pub_leisure-sk", detail={}),
    ]
    assert filter_options(MONITOR, rows, "detail.error_code") == ["404", "MISSING_ITEMS"]

    only_404 = apply_filters(MONITOR, rows, selections={"detail.error_code": "404"})
    assert [i.publisher_id for i in only_404] == ["pub_everyone-active"]
