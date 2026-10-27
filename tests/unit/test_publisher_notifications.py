"""The notification alert rule, matching the API's `alerts` count."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from stewards.api.models import PublisherNotification, PublisherNotificationSummary
from stewards.monitors.overview import NavBadge
from stewards.monitors.publisher_notifications import (
    COLUMNS,
    FOLLOW_UP_DAYS,
    NOTES_MAX,
    STATUSES,
    alert_reasons,
    alerts_badge,
    apply_edits,
    apply_filters,
    filter_options,
    has_alert,
    problem_frame,
    sort_rows,
    summary_stats,
    to_dataframe,
    tone_frame,
)
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import EMPTY

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


# --- table, filters and KPIs --------------------------------------------------------------


def listing() -> list[PublisherNotification]:
    return [
        row(id="pn_a", publisher_name="Alpha", oldest_days_open=3),
        row(id="pn_b", publisher_name="Bravo", oldest_days_open=20),
        row(id="pn_c", publisher_name="Charlie", date_contacted=None, oldest_days_open=1),
        row(id="pn_d", publisher_name="Delta", status="closed", stakeholder="Council"),
    ]


def test_sort_puts_alerts_first_then_oldest() -> None:
    ordered = sort_rows(listing(), TODAY)
    assert [r.id for r in ordered] == ["pn_c", "pn_b", "pn_a", "pn_d"]


def test_closed_rows_are_hidden_unless_asked_for() -> None:
    assert "pn_d" not in [r.id for r in apply_filters(listing())]
    assert "pn_d" in [r.id for r in apply_filters(listing(), show_closed=True)]


def test_filters_combine() -> None:
    kept = apply_filters(listing(), search="  BRA ", status="in_progress")
    assert [r.id for r in kept] == ["pn_b"]
    assert apply_filters(listing(), stakeholder="Council", show_closed=True)[0].id == "pn_d"


def test_filter_options_skip_blanks() -> None:
    assert filter_options(listing()) == (["closed", "in_progress"], ["Council"])


def test_table_and_tones_line_up() -> None:
    rows = sort_rows(listing(), TODAY)
    frame, tones = to_dataframe(rows, TODAY), tone_frame(rows)

    assert list(frame.columns) == list(COLUMNS) == list(tones.columns)
    assert len(frame) == len(tones) == 4
    assert frame.iloc[0]["Alert"] == "Never contacted"
    assert frame.iloc[2]["Alert"] == EMPTY
    assert tones.iloc[1]["Oldest open"] == Tone.RED.value
    assert tones.iloc[3]["Status"] == Tone.GREEN.value


def test_summary_nulls_read_as_em_dash_not_zero() -> None:
    stats = summary_stats(PublisherNotificationSummary(alerts=0))
    assert stats[0] == ("need attention", "0", Tone.GREEN)
    assert stats[1][1] == EMPTY


def test_problem_frame_marks_past_threshold() -> None:
    busy = row(problems=[problem("dataset_stall", 7), problem("dataset_stall", 2)])
    frame = problem_frame(busy)
    assert list(frame["Past threshold"]) == ["Yes", "No"]


def test_problem_frame_of_an_empty_row_is_empty() -> None:
    assert problem_frame(row()).empty


# --- editing ------------------------------------------------------------------------------


def edit(target: PublisherNotification | None = None, **changes: Any) -> Any:
    """`apply_edits` with valid defaults, so each test changes only what it is about."""
    fields: dict[str, Any] = {
        "status": "in_progress",
        "stakeholder": "Council",
        "contact": "bookings@example.org",
        "date_contacted": TODAY,
        "notes": "Emailed about the stalled feed.",
        "today": TODAY,
    }
    return apply_edits(target or row(), **{**fields, **changes})


def test_valid_edit_returns_a_body_and_no_errors() -> None:
    body, errors = edit()
    assert errors == []
    assert body is not None
    assert (body.status, body.contact) == ("in_progress", "bookings@example.org")


def test_edit_keeps_every_field_the_form_does_not_show() -> None:
    original = row(
        publisher_id="dummy-leisure",
        problem_count=2,
        monitors=["dataset_stall"],
        oldest_days_open=12,
        problems=[problem("dataset_stall", 7)],
    )
    body, _ = edit(original, status="closed")

    assert body is not None
    assert body.problems == original.problems_raw
    assert body.monitors == ("dataset_stall",)
    assert (body.problem_count, body.oldest_days_open) == (2, 12)
    assert body.publisher_id == "dummy-leisure"


def test_blank_boxes_are_saved_as_null() -> None:
    body, _ = edit(stakeholder="   ", contact="", notes=None)
    assert body is not None
    assert (body.stakeholder, body.contact, body.notes) == (None, None, None)


def test_text_is_trimmed() -> None:
    body, _ = edit(contact="  bookings@example.org ")
    assert body is not None
    assert body.contact == "bookings@example.org"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "done"}, "Status must be one of"),
        ({"contact": "Jo Bloggs"}, "Contact must be an email address"),
        ({"contact": "a@b"}, "Contact must be an email address"),
        ({"date_contacted": date(2026, 10, 14)}, "cannot be in the future"),
        ({"notes": "x" * (NOTES_MAX + 1)}, "2,000 characters or fewer"),
    ],
)
def test_invalid_edit_is_refused(changes: dict[str, Any], message: str) -> None:
    body, errors = edit(**changes)
    assert body is None
    assert len(errors) == 1
    assert message in errors[0]


def test_every_problem_is_reported_at_once() -> None:
    _, errors = edit(status="done", contact="nope", notes="x" * (NOTES_MAX + 1))
    assert len(errors) == 3


def test_notes_at_the_limit_are_allowed() -> None:
    body, errors = edit(notes="x" * NOTES_MAX)
    assert errors == []
    assert body is not None


def test_statuses_are_the_workflow_three() -> None:
    assert STATUSES == ("open", "in_progress", "closed")


# --- sidebar ------------------------------------------------------------------------------


def test_alerts_badge_is_a_red_count() -> None:
    badge = alerts_badge(PublisherNotificationSummary(alerts=1200))
    assert badge == NavBadge("1,200", Tone.RED)


@pytest.mark.parametrize("alerts", [0, None])
def test_no_badge_when_nothing_needs_attention(alerts: int | None) -> None:
    assert alerts_badge(PublisherNotificationSummary(alerts=alerts)) is None
