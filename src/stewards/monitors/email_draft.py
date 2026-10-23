"""The publisher email draft.

Publisher-facing copy, so it is a golden-file test in `tests/unit/test_email_draft.py`.
The app never sends: this string exists only to be copied out of the popover.
"""

from __future__ import annotations

from datetime import date, timedelta

from stewards.api.models import Incident, PublisherNotification
from stewards.monitors.registry import Monitor, get_monitor
from stewards.monitors.transforms import EMPTY, parse_detail, resolve_field

REPLY_WINDOW_DAYS = 5

_OBSERVATIONS = {
    "dataset_stall": (
        "every feed in this dataset has stopped publishing new or updated items, and has "
        "been silent for {days} days. The last change we recorded on any of them was "
        "{evidence}. Nothing new is reaching the services that consume your data."
    ),
    "dataset_orphaned_children": (
        "{evidence} items in this dataset reference a parent that the feed does not "
        "contain: ScheduledSessions whose superEvent is missing from the SessionSeries "
        "feed, or Slots without their FacilityUse. A consumer cannot display these items "
        "at all, because the parent carries the name, location and activity."
    ),
    "dataset_future_decline": (
        "we have recorded a falling count of opportunities starting in the future for "
        "{days} days: {evidence} fewer items are visible to consumers now than at the start "
        "of the window we compare against. A decline of this shape usually means items are "
        "being deleted or allowed to expire faster than new ones are published."
    ),
    "single_feed_stall": (
        "the feed still responds with HTTP 200, but the most recent modified timestamp "
        "has not advanced for {days} days. The last change we recorded was {evidence}."
    ),
    "feed_ingestion_error": (
        "our daily crawl has failed to ingest the feed on {days} consecutive days, either "
        "because the endpoint did not respond correctly or because the page it returned "
        "could not be parsed. The last successful ingestion was {evidence}."
    ),
}

_DEFAULT_OBSERVATION = (
    "this {entity} has been failing the {monitor} check for {days} days. "
    "First detected {evidence}."
)

_EVIDENCE_FIELDS = {
    "single_feed_stall": "last_modified",
    "feed_ingestion_error": "last_completed",
    "dataset_orphaned_children": "orphan_count",
    "dataset_stall": "last_modified",
    "dataset_future_decline": "drop",
}


def _evidence(monitor: Monitor, incident: Incident) -> str:
    """The figure or date the observation sentence cites.

    Falls back to when we first saw the incident, and to em dash for a monitor that measures
    a snapshot and so has no first-detected date either.
    """
    attr = _EVIDENCE_FIELDS.get(monitor.id)
    value = getattr(parse_detail(monitor, incident), attr, None) if attr else None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return f"{value:,}"
    if incident.first_detected is not None:
        return incident.first_detected.isoformat()
    return EMPTY


def _first_detected(incident: Incident) -> str:
    """When we first saw it and how long it has been open, where the monitor reports them.

    A monitor that measures a snapshot has neither, and an invented date in a publisher
    email is worse than an absent one.
    """
    if incident.first_detected is None:
        return f"{EMPTY} (this monitor reports a snapshot, not an age)"
    if incident.days_open is None:
        return incident.first_detected.isoformat()
    return f"{incident.first_detected.isoformat()} ({incident.days_open} days open)"


def _observation(monitor: Monitor, incident: Incident) -> str:
    """The sentence saying what we saw, in the monitor's own publisher-facing words."""
    template = _OBSERVATIONS.get(monitor.id, _DEFAULT_OBSERVATION)
    return template.format(
        days=incident.days_open,
        evidence=_evidence(monitor, incident),
        monitor=monitor.name.lower(),
        entity=monitor.entity,
    )


def subject_line(monitor: Monitor, incident: Incident) -> str:
    """Names whatever the monitor says identifies one of its incidents."""
    named = resolve_field(monitor, incident, monitor.summary_field)
    subject = str(named) if named not in (None, "") else f"OpenActive {monitor.entity}"
    return f"OpenActive data check: {subject} — {monitor.name.lower()}"


def _identifying_lines(monitor: Monitor, incident: Incident) -> list[str]:
    """The block naming what we are writing about, as the monitor declares it."""
    lines = []
    for label, path in monitor.email_fields:
        value = resolve_field(monitor, incident, path)
        lines.append(f"{label}: {value if value not in (None, '') else EMPTY}")
    return lines


def draft_email(
    monitor: Monitor,
    incident: Incident,
    snapshot_date: date,
    *,
    sender_name: str = "The ODI data stewards team",
) -> str:
    """A copyable message: what we observed, for how long, and what we need by when."""
    observation = _observation(monitor, incident)
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
            *_identifying_lines(monitor, incident),
            f"First detected: {_first_detected(incident)}",
            "",
            f"Could you confirm whether the export that populates this {monitor.entity} is "
            "still running,",
            f"and let us know by {reply_by} if you need help investigating.",
            "",
            f"No action is needed on our side once the {monitor.entity} resumes; the check "
            "clears itself",
            "on the next daily snapshot.",
            "",
            "Thank you,",
            sender_name,
        ]
    )


def _known_problems(row: PublisherNotification) -> list[tuple[Monitor, Incident]]:
    """Each problem with its monitor, skipping a monitor this build does not register.

    An unknown monitor has no wording to describe it, and a guess in a publisher email is
    worse than leaving it out; the steward still sees it in the problem table.
    """
    pairs = []
    for problem in row.problems:
        try:
            monitor = get_monitor(problem.monitor_id)
        except KeyError:
            continue
        pairs.append((monitor, problem.as_incident(row.publisher_id, row.publisher_name)))
    return pairs


def draft_publisher_email(
    row: PublisherNotification,
    snapshot_date: date,
    *,
    sender_name: str = "The ODI data stewards team",
) -> str:
    """One message covering every problem on the row; empty if none can be described.

    A single problem reads exactly as the per-incident draft does.
    """
    pairs = _known_problems(row)
    if not pairs:
        return ""
    if len(pairs) == 1:
        monitor, incident = pairs[0]
        return draft_email(monitor, incident, snapshot_date, sender_name=sender_name)

    reply_by = (snapshot_date + timedelta(days=REPLY_WINDOW_DAYS)).isoformat()
    sections: list[str] = []
    for number, (monitor, incident) in enumerate(pairs, start=1):
        sections += [
            f"{number}. {monitor.name}: {_observation(monitor, incident)}",
            *_identifying_lines(monitor, incident),
            f"First detected: {_first_detected(incident)}",
            "",
        ]

    return "\n".join(
        [
            f"Subject: OpenActive data check: {row.publisher_name} — {len(pairs)} issues",
            "",
            f"Hello {row.publisher_name} team,",
            "",
            "We monitor the OpenActive feeds you publish as part of the open data service.",
            f"In our snapshot of {snapshot_date.isoformat()}, we found {len(pairs)} issues "
            "with your data.",
            "",
            *sections,
            "Could you look into each of these,",
            f"and let us know by {reply_by} if you need help investigating.",
            "",
            "No action is needed on our side once each is fixed; the checks clear themselves",
            "on the next daily snapshot.",
            "",
            "Thank you,",
            sender_name,
        ]
    )
