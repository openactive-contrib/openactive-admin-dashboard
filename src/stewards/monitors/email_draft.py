"""The publisher email draft.

Publisher-facing copy, so it is a golden-file test in `tests/unit/test_email_draft.py`.
The app never sends: this string exists only to be copied out of the popover.
"""

from __future__ import annotations

from datetime import date, timedelta

from stewards.api.models import Incident
from stewards.monitors.registry import Monitor
from stewards.monitors.transforms import EMPTY, parse_detail

REPLY_WINDOW_DAYS = 5

_OBSERVATIONS = {
    "single_feed_stall": (
        "the feed still responds with HTTP 200, but the most recent modified timestamp "
        "has not advanced for {days} days. The last change we recorded was {evidence}."
    ),
    "http_failure": (
        "the feed endpoint has failed to respond correctly on {days} consecutive daily "
        "fetches. The last successful fetch was {evidence}."
    ),
}

_DEFAULT_OBSERVATION = (
    "this feed has been failing the {monitor} check for {days} days. First detected {evidence}."
)

_EVIDENCE_FIELDS = {
    "single_feed_stall": "last_modified",
    "http_failure": "last_success",
}


def _evidence(monitor: Monitor, incident: Incident) -> str:
    attr = _EVIDENCE_FIELDS.get(monitor.id)
    value = getattr(parse_detail(monitor, incident), attr, None) if attr else None
    return value.isoformat() if isinstance(value, date) else incident.first_detected.isoformat()


def subject_line(monitor: Monitor, incident: Incident) -> str:
    feed = incident.feed_name or "OpenActive feed"
    return f"OpenActive data check: {feed} — {monitor.name.lower()}"


def draft_email(
    monitor: Monitor,
    incident: Incident,
    snapshot_date: date,
    *,
    sender_name: str = "The ODI data stewards team",
) -> str:
    """A copyable message: what we observed, for how long, and what we need by when."""
    template = _OBSERVATIONS.get(monitor.id, _DEFAULT_OBSERVATION)
    observation = template.format(
        days=incident.days_open,
        evidence=_evidence(monitor, incident),
        monitor=monitor.name.lower(),
    )
    reply_by = (snapshot_date + timedelta(days=REPLY_WINDOW_DAYS)).isoformat()

    return "\n".join(
        [
            f"Subject: {subject_line(monitor, incident)}",
            "",
            f"Hello {incident.publisher_name} team,",
            "",
            "We monitor the OpenActive feeds you publish as part of the open data service.",
            f"In our snapshot of {snapshot_date.isoformat()}, {observation}",
            "",
            f"Feed: {incident.feed_name or EMPTY}",
            f"Feed type: {incident.feed_type or EMPTY}",
            f"Endpoint: {incident.feed_url or EMPTY}",
            f"First detected: {incident.first_detected.isoformat()} "
            f"({incident.days_open} days open)",
            "",
            "Could you confirm whether the export that populates this feed is still running,",
            f"and let us know by {reply_by} if you need help investigating.",
            "",
            "No action is needed on our side once the feed resumes; the check clears itself",
            "on the next daily snapshot.",
            "",
            "Thank you,",
            sender_name,
        ]
    )
