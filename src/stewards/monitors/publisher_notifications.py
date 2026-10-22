"""Rules for the publisher notification list. Pure: no Streamlit, no I/O, no clock.

`today` is always passed in, so the tests can pin the date.
"""

from __future__ import annotations

from datetime import date

from stewards.alerts.checks import CHECKS, Check
from stewards.api.models import NotificationProblem, PublisherNotification

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
