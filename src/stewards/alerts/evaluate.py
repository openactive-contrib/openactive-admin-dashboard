"""Incidents -> the ones a check reports today. Pure: the clock is passed in."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from stewards.alerts.checks import Check
from stewards.api.models import Incident
from stewards.monitors.registry import get_monitor
from stewards.monitors.transforms import resolve_field

UK = ZoneInfo("Europe/London")


@dataclass(frozen=True, slots=True)
class Hit:
    """One reported incident, already resolved into what the message prints."""

    publisher: str
    subject: str | None
    feed_type: str | None
    value: int
    link: str | None


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: Check
    hits: tuple[Hit, ...] = ()
    error: str | None = None
    """Set when the monitor's read failed; the digest says so instead of claiming zero."""


@dataclass(frozen=True, slots=True)
class Digest:
    today: date
    results: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return sum(len(r.hits) for r in self.results)


def uk_today(now: datetime) -> date:
    return now.astimezone(UK).date()


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def select(check: Check, incidents: Sequence[Incident], today: date) -> CheckResult:
    monitor = get_monitor(check.monitor_id)
    hits = []
    for incident in incidents:
        if not check.matches_feed_type(incident.feed_type):
            continue
        value = resolve_field(monitor, incident, check.field)
        if not check.matches_value(value, today):
            continue
        hits.append(
            Hit(
                publisher=incident.publisher_name,
                subject=_text(resolve_field(monitor, incident, monitor.summary_field)),
                feed_type=incident.feed_type,
                value=value,
                link=_text(resolve_field(monitor, incident, check.link_field)),
            )
        )
    hits.sort(key=lambda h: (h.publisher.casefold(), h.subject or ""))
    return CheckResult(check=check, hits=tuple(hits))
