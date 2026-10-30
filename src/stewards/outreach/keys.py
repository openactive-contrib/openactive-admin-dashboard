"""Identity: which dataset an incident belongs to, and which incident a stored problem is.

Both are read off the data rather than declared per rule. A feed-level incident (it has a
`feed_id`) is keyed by its feed; a dataset-level one by its dataset URL. A problem a steward
typed by hand goes through the same function, via `NotificationProblem.as_incident`, so the
job recognises it as the incident it describes and does not raise it a second time.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from stewards.api.models import Incident, NotificationProblem
from stewards.monitors.registry import get_monitor
from stewards.monitors.transforms import resolve_field

#: `Incident` fields a stored problem does not carry: the row owns the publisher and the
#: workflow state, and the job's own today-only flags would be stale by tomorrow.
_NOT_STORED = frozenset(
    {"publisher_id", "publisher_name", "status", "past_threshold", "last_contacted", "trend"}
)


def dataset_url(incident: Incident) -> str | None:
    """The dataset the incident belongs to; None when the API did not say."""
    try:
        monitor = get_monitor(incident.monitor_id)
    except KeyError:
        return None
    value = resolve_field(monitor, incident, "detail.dataset_url")
    return (value.strip() or None) if isinstance(value, str) else None


def incident_key(incident: Incident) -> str | None:
    """A stable name for the incident across days, or None if it has nothing to key on."""
    if incident.feed_id:
        return f"{incident.monitor_id}:feed:{incident.feed_id}"
    url = dataset_url(incident)
    return f"{incident.monitor_id}:dataset:{url}" if url else None


def stored_key(
    problem: NotificationProblem, publisher_id: str, publisher_name: str
) -> str | None:
    """A stored problem's key: the one the job wrote, else derived as for an incident."""
    return problem.incident_key or incident_key(
        problem.as_incident(publisher_id, publisher_name)
    )


def problem_payload(
    incident: Incident, *, key: str, recorded_on: date, rule_id: str | None
) -> dict[str, Any]:
    """The incident as a stored problem: today's evidence plus the job's bookkeeping.

    It reads back through `NotificationProblem`, so the page and the email draft see it the
    same way they see a hand-typed problem.
    """
    payload = incident.model_dump(mode="json", exclude=set(_NOT_STORED), exclude_none=True)
    payload["incident_key"] = key
    payload["recorded_on"] = recorded_on.isoformat()
    if rule_id is not None:
        payload["rule_id"] = rule_id
    return payload
