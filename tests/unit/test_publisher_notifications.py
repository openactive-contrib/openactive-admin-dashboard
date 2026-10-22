"""The notification alert rule, matching the API's `alerts` count."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from stewards.api.models import PublisherNotification
from stewards.monitors.publisher_notifications import (
    FOLLOW_UP_DAYS,
    alert_reasons,
    has_alert,
)

TODAY = date(2026, 10, 13)


def row(**overrides: Any) -> PublisherNotification:
    """A contacted, in-progress row with no problems: no alert unless a test adds one."""
    payload: dict[str, Any] = {
        "id": "pn_dummy",
        "publisher_name": "Dummy Leisure",
        "status": "in_progress",
        "date_contacted": TODAY.isoformat(),
        "problems": [],
    }
    return PublisherNotification.model_validate({**payload, **overrides})


def problem(monitor_id: str, days_open: int, feed_type: str = "Slot") -> dict[str, Any]:
    return {"monitor_id": monitor_id, "days_open": days_open, "feed_type": feed_type}


def test_quiet_row_has_no_alert() -> None:
    assert alert_reasons(row(), TODAY) == ()


def test_never_contacted_alerts() -> None:
    assert alert_reasons(row(date_contacted=None), TODAY) == ("Never contacted",)


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [(FOLLOW_UP_DAYS, False), (FOLLOW_UP_DAYS + 1, True)],
)
def test_follow_up_is_strictly_older_than_five_days(days_ago: int, expected: bool) -> None:
    contacted = date.fromordinal(TODAY.toordinal() - days_ago).isoformat()
    assert has_alert(row(date_contacted=contacted), TODAY) is expected


@pytest.mark.parametrize("status", ["closed", "Closed", "something_else"])
def test_inactive_rows_never_alert(status: str) -> None:
    stale = row(status=status, date_contacted=None, problems=[problem("dataset_stall", 30)])
    assert alert_reasons(stale, TODAY) == ()


def test_status_is_read_case_insensitively() -> None:
    assert has_alert(row(status=" Open ", date_contacted=None), TODAY)


@pytest.mark.parametrize(
    ("monitor_id", "threshold"),
    [("dataset_stall", 5), ("single_feed_stall", 10), ("feed_ingestion_error", 10)],
)
def test_problem_alerts_at_its_slack_threshold(monitor_id: str, threshold: int) -> None:
    assert not has_alert(row(problems=[problem(monitor_id, threshold - 1)]), TODAY)
    assert has_alert(row(problems=[problem(monitor_id, threshold)]), TODAY)


def test_single_feed_stall_only_counts_slot_and_scheduled_session() -> None:
    facility = problem("single_feed_stall", 30, feed_type="FacilityUse")
    session = problem("single_feed_stall", 30, feed_type="ScheduledSession")
    assert not has_alert(row(problems=[facility]), TODAY)
    assert has_alert(row(problems=[session]), TODAY)


def test_monitor_without_a_slack_check_never_alerts() -> None:
    assert not has_alert(row(problems=[problem("feed_quality", 99)]), TODAY)


def test_reasons_name_every_cause() -> None:
    busy = row(date_contacted=None, problems=[problem("dataset_stall", 7)])
    assert alert_reasons(busy, TODAY) == (
        "Never contacted",
        "Dataset-wide stalls: 7 days without new data",
    )
