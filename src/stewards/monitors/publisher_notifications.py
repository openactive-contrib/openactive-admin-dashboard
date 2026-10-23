"""Rules for the publisher notification list. Pure: no Streamlit, no I/O, no clock.

`today` is always passed in, so the tests can pin the date.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

import pandas as pd

from stewards.alerts.checks import CHECKS, Check
from stewards.api.models import (
    NotificationProblem,
    PublisherNotification,
    PublisherNotificationBody,
    PublisherNotificationSummary,
)
from stewards.monitors.overview import NavBadge
from stewards.monitors.registry import RAG_KINDS, Col, ColKind, get_monitor
from stewards.monitors.thresholds import Tone, days_label, days_tone, status_label, status_tone
from stewards.monitors.transforms import EMPTY

#: A contacted row needs chasing once the last contact is strictly older than this.
FOLLOW_UP_DAYS = 5

#: Statuses a steward is still working. A closed row never alerts.
ACTIVE_STATUSES = frozenset({"open", "in_progress"})

_CHECKS_BY_MONITOR: dict[str, Check] = {check.monitor_id: check for check in CHECKS}


def is_active(row: PublisherNotification) -> bool:
    return row.status.strip().lower() in ACTIVE_STATUSES


def problem_alert(problem: NotificationProblem) -> str | None:
    """Why this problem is past its Slack threshold, or None if it is not."""
    check = _CHECKS_BY_MONITOR.get(problem.monitor_id)
    if check is None or problem.days_open is None:
        return None
    if not check.matches_feed_type(problem.feed_type):
        return None
    if problem.days_open < check.threshold:
        return None
    return f"{check.label}: {check.describe(problem.days_open)}"


def alert_reasons(row: PublisherNotification, today: date) -> tuple[str, ...]:
    """Every reason this row needs attention today. Empty means no alert."""
    if not is_active(row):
        return ()
    reasons: list[str] = []
    if row.date_contacted is None:
        reasons.append("Never contacted")
    else:
        days_since = (today - row.date_contacted).days
        if days_since > FOLLOW_UP_DAYS:
            reasons.append(f"No follow-up for {days_since} days")
    for problem in row.problems:
        reason = problem_alert(problem)
        if reason is not None:
            reasons.append(reason)
    return tuple(reasons)


def has_alert(row: PublisherNotification, today: date) -> bool:
    return bool(alert_reasons(row, today))


#: The oldest problem goes amber from halfway to, and red at, the lowest Slack threshold.
OLDEST_TONE_DAYS = min(check.threshold for check in CHECKS)

COLUMN_SPECS: tuple[Col, ...] = (
    Col("alert", "Alert", ColKind.TEXT, help="Why this publisher needs attention today"),
    Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
    Col("problem_count", "Problems", ColKind.NUMBER),
    Col("monitors", "Monitors", ColKind.TEXT),
    Col("oldest_days_open", "Oldest open", ColKind.DAYS),
    Col("status", "Status", ColKind.STATUS),
    Col("stakeholder", "Stakeholder", ColKind.TEXT),
    Col("date_contacted", "Last contacted", ColKind.DATE),
)

COLUMNS: tuple[str, ...] = tuple(col.label for col in COLUMN_SPECS)
RAG_COLUMNS: tuple[str, ...] = tuple(col.label for col in COLUMN_SPECS if col.kind in RAG_KINDS)


def monitor_name(monitor_id: str) -> str:
    """The registry's name for a monitor; the raw id for one this build does not know."""
    try:
        return get_monitor(monitor_id).name
    except KeyError:
        return monitor_id


def sort_rows(
    rows: Sequence[PublisherNotification], today: date
) -> list[PublisherNotification]:
    """Alerts first, then the oldest problem, then publisher name."""
    return sorted(
        rows,
        key=lambda row: (
            not has_alert(row, today),
            -(row.oldest_days_open or 0),
            row.publisher_name.lower(),
        ),
    )


def filter_options(rows: Sequence[PublisherNotification]) -> tuple[list[str], list[str]]:
    """The statuses and stakeholders present on the list, for the filter dropdowns."""
    statuses = sorted({row.status for row in rows if row.status})
    stakeholders = sorted({row.stakeholder for row in rows if row.stakeholder})
    return statuses, stakeholders


def apply_filters(
    rows: Sequence[PublisherNotification],
    *,
    search: str = "",
    status: str = "",
    stakeholder: str = "",
    show_closed: bool = False,
) -> list[PublisherNotification]:
    """The rows the steward asked for. An empty filter means "any"."""
    needle = search.strip().lower()
    kept = []
    for row in rows:
        if not show_closed and row.status.strip().lower() == "closed":
            continue
        if status and row.status != status:
            continue
        if stakeholder and row.stakeholder != stakeholder:
            continue
        haystack = f"{row.publisher_name} {row.publisher_id} {row.contact or ''}".lower()
        if needle and needle not in haystack:
            continue
        kept.append(row)
    return kept


def _date_text(value: date | None) -> str:
    return value.isoformat() if value else EMPTY


def to_dataframe(rows: Sequence[PublisherNotification], today: date) -> pd.DataFrame:
    """One line per row, in the order given. The page sorts and filters first."""
    lines = [
        {
            "Alert": "; ".join(alert_reasons(row, today)) or EMPTY,
            "Publisher": row.publisher_name or row.publisher_id,
            "Problems": row.problem_count,
            "Monitors": ", ".join(monitor_name(m) for m in row.monitors) or EMPTY,
            "Oldest open": (
                EMPTY if row.oldest_days_open is None else days_label(row.oldest_days_open)
            ),
            "Status": status_label(row.status) if row.status else EMPTY,
            "Stakeholder": row.stakeholder or EMPTY,
            "Last contacted": _date_text(row.date_contacted),
        }
        for row in rows
    ]
    return pd.DataFrame(lines, columns=list(COLUMNS))


def tone_frame(rows: Sequence[PublisherNotification]) -> pd.DataFrame:
    """Colour per cell for the coloured columns, aligned line for line with `to_dataframe`."""
    lines = [
        {
            **dict.fromkeys(COLUMNS, ""),
            "Oldest open": (
                ""
                if row.oldest_days_open is None
                else days_tone(row.oldest_days_open, OLDEST_TONE_DAYS).value
            ),
            "Status": status_tone(row.status).value,
        }
        for row in rows
    ]
    return pd.DataFrame(lines, columns=list(COLUMNS))


def _count(value: int | None) -> str:
    return EMPTY if value is None else f"{value:,}"


def summary_stats(summary: PublisherNotificationSummary) -> tuple[tuple[str, str, Tone], ...]:
    """The KPI tiles: label, figure, colour. A figure the API did not send reads as em dash."""
    alerts = summary.alerts
    return (
        ("need attention", _count(alerts), Tone.RED if alerts else Tone.GREEN),
        ("open", _count(summary.open), Tone.GREY),
        ("in progress", _count(summary.in_progress), Tone.AMBER),
        ("never contacted", _count(summary.never_contacted), Tone.GREY),
        ("closed", _count(summary.closed), Tone.GREEN),
    )


PROBLEM_COLUMNS: tuple[str, ...] = (
    "Monitor",
    "Feed",
    "Feed type",
    "Days open",
    "First detected",
    "Past threshold",
)


def problem_frame(row: PublisherNotification) -> pd.DataFrame:
    """The selected publisher's problems, one line each."""
    lines = [
        {
            "Monitor": monitor_name(problem.monitor_id),
            "Feed": problem.feed_name or problem.feed_id or EMPTY,
            "Feed type": problem.feed_type or EMPTY,
            "Days open": EMPTY if problem.days_open is None else days_label(problem.days_open),
            "First detected": _date_text(problem.first_detected),
            "Past threshold": "Yes" if problem_alert(problem) else "No",
        }
        for problem in row.problems
    ]
    return pd.DataFrame(lines, columns=list(PROBLEM_COLUMNS))


# --- editing ------------------------------------------------------------------------------

#: The statuses a steward can choose, in the order the dropdown shows them.
STATUSES: tuple[str, ...] = ("open", "in_progress", "closed")

NOTES_MAX = 2000

#: Deliberately loose: something@something.something, with no spaces. Real checking is
#: sending the email; this only catches a name typed into the wrong box.
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _clean(value: str | None) -> str | None:
    """Trimmed text, or None for a blank box, so the API stores NULL, not an empty string."""
    if value is None:
        return None
    return value.strip() or None


def apply_edits(
    row: PublisherNotification,
    *,
    status: str,
    stakeholder: str | None,
    contact: str | None,
    date_contacted: date | None,
    notes: str | None,
    today: date,
) -> tuple[PublisherNotificationBody | None, list[str]]:
    """The full PATCH body with the form's five fields applied, or the reasons it is invalid.

    Every other field is copied from `row`, because the API overwrites every column.
    """
    errors: list[str] = []
    contact = _clean(contact)
    notes = _clean(notes)

    if status not in STATUSES:
        errors.append(f"Status must be one of: {', '.join(STATUSES)}.")
    if contact is not None and not _EMAIL.fullmatch(contact):
        errors.append("Contact must be an email address.")
    if date_contacted is not None and date_contacted > today:
        errors.append("Date contacted cannot be in the future.")
    if notes is not None and len(notes) > NOTES_MAX:
        errors.append(f"Notes must be {NOTES_MAX:,} characters or fewer (now {len(notes):,}).")

    if errors:
        return None, errors

    body = row.to_body().model_copy(
        update={
            "status": status,
            "stakeholder": _clean(stakeholder),
            "contact": contact,
            "date_contacted": date_contacted,
            "notes": notes,
        }
    )
    return body, []


# --- sidebar ------------------------------------------------------------------------------


def alerts_badge(summary: PublisherNotificationSummary) -> NavBadge | None:
    """The sidebar pill: rows needing attention today. None when there are none to show."""
    if summary.alerts is None or summary.alerts <= 0:
        return None
    return NavBadge(f"{summary.alerts:,}", Tone.RED)
