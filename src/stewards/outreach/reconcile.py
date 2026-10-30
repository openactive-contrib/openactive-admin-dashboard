"""What to write to the notification list, given today's findings and its current rows. Pure.

One active row per publisher. A raised finding is appended to the publisher's active row,
or opens a new row if there is none; a finding whose incident is already on any active row
is not raised again. Every stored problem the job can recognise is refreshed with today's
evidence, or dropped once its incident has closed. Problems it cannot recognise — typed by
hand for a monitor it does not read, or with nothing to key on — are kept exactly as stored.

Closed rows are never touched, so an incident that reopens after its row was closed is
raised again on a new row.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from stewards.api.models import (
    Incident,
    NotificationProblem,
    PublisherNotification,
    PublisherNotificationBody,
)
from stewards.monitors.publisher_notifications import is_active
from stewards.outreach.decide import Finding, Snapshot
from stewards.outreach.keys import problem_payload, stored_key

NEW_ROW_STATUS = "open"

#: The admin API's publisher id for a dataset whose publisher it could not name. A row for
#: it would gather unrelated datasets under no one to contact, so such a finding is reported
#: and not raised.
UNKNOWN_PUBLISHER_ID = "pub_unknown"


@dataclass(frozen=True, slots=True)
class Create:
    body: PublisherNotificationBody
    added: tuple[Finding, ...]

    @property
    def publisher_name(self) -> str:
        return self.body.publisher_name or ""


@dataclass(frozen=True, slots=True)
class Update:
    row: PublisherNotification
    body: PublisherNotificationBody
    added: tuple[Finding, ...] = ()
    refreshed: int = 0
    dropped: int = 0
    resolved: bool = False
    """The row had problems and now has none: a steward should review and close it."""

    @property
    def publisher_name(self) -> str:
        return self.body.publisher_name or self.row.id


Action = Create | Update


@dataclass(frozen=True, slots=True)
class Skipped:
    finding: Finding
    reason: str


@dataclass(frozen=True, slots=True)
class Plan:
    actions: tuple[Action, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    unreadable: tuple[str, ...] = ()
    """Ids of active rows whose stored problems are not a list: left alone entirely."""


@dataclass(slots=True)
class _Draft:
    """An active row's problems as they will be saved, built up item by item."""

    row: PublisherNotification
    before: list[Any]
    after: list[Any] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)
    added: list[Finding] = field(default_factory=list)
    refreshed: int = 0
    dropped: int = 0


def reconcile(
    snapshot: Snapshot,
    findings: Iterable[Finding],
    rows: Sequence[PublisherNotification],
    today: date,
) -> Plan:
    open_by_key = snapshot.by_key()
    drafts: list[_Draft] = []
    unreadable: list[PublisherNotification] = []
    for row in rows:
        if not is_active(row):
            continue
        raw = [] if row.problems_raw is None else row.problems_raw
        if not isinstance(raw, list):
            unreadable.append(row)
            continue
        drafts.append(_refresh(row, raw, snapshot, open_by_key, today))

    claimed = {key: draft.row.id for draft in drafts for key in draft.keys}
    target = _target_rows(drafts)
    blocked = {row.publisher_id for row in unreadable}
    new_rows: dict[str, list[Finding]] = {}
    skipped: list[Skipped] = []

    for finding in findings:
        reason = _skip_reason(finding, claimed, blocked)
        if reason is not None:
            skipped.append(Skipped(finding, reason))
            continue
        key = finding.key
        assert key is not None  # _skip_reason rejects a finding with no key
        publisher_id = finding.incident.publisher_id
        payload = problem_payload(
            finding.incident, key=key, recorded_on=today, rule_id=finding.rule.id
        )
        draft = target.get(publisher_id)
        if draft is not None:
            draft.after.append(payload)
            draft.added.append(finding)
            claimed[key] = draft.row.id
        else:
            new_rows.setdefault(publisher_id, []).append(finding)
            claimed[key] = f"the new row for {finding.incident.publisher_name}"

    taken = {row.id for row in rows}
    actions: list[Action] = [
        update for draft in drafts if (update := _update(draft, today)) is not None
    ]
    for added in new_rows.values():
        create = _create(added, today, taken)
        taken.add(create.body.id)
        actions.append(create)
    actions.sort(key=lambda action: action.publisher_name.casefold())
    return Plan(tuple(actions), tuple(skipped), tuple(row.id for row in unreadable))


def _refresh(
    row: PublisherNotification,
    raw: list[Any],
    snapshot: Snapshot,
    open_by_key: dict[str, Incident],
    today: date,
) -> _Draft:
    draft = _Draft(row, list(raw))
    for item in raw:
        problem = _parse(item)
        key = (
            None
            if problem is None
            else stored_key(problem, row.publisher_id, row.publisher_name)
        )
        if problem is None or key is None or problem.monitor_id not in snapshot.monitor_ids:
            draft.after.append(item)
            if key is not None:
                draft.keys.add(key)
            continue
        incident = open_by_key.get(key)
        if incident is None:
            draft.dropped += 1
            continue
        draft.after.append(
            problem_payload(
                incident,
                key=key,
                recorded_on=problem.recorded_on or today,
                rule_id=problem.rule_id,
            )
        )
        draft.keys.add(key)
        draft.refreshed += 1
    return draft


def _parse(item: Any) -> NotificationProblem | None:
    """A stored item as a problem. The row model has already validated every dict in it."""
    return NotificationProblem.model_validate(item) if isinstance(item, dict) else None


def _target_rows(drafts: Sequence[_Draft]) -> dict[str, _Draft]:
    """Each publisher's earliest active row: the one new problems are appended to."""
    target: dict[str, _Draft] = {}
    for draft in sorted(drafts, key=lambda d: (_created(d.row), d.row.id)):
        if draft.row.publisher_id:
            target.setdefault(draft.row.publisher_id, draft)
    return target


def _created(row: PublisherNotification) -> datetime:
    return row.created_at.replace(tzinfo=None) if row.created_at else datetime.max


def _skip_reason(finding: Finding, claimed: dict[str, str], blocked: set[str]) -> str | None:
    if finding.suppressed_by is not None:
        return f"suppressed by {finding.suppressed_by} on the same dataset"
    if finding.key is None:
        return "no feed id or dataset URL to recognise it by"
    publisher_id = finding.incident.publisher_id.strip()
    if not publisher_id or publisher_id == UNKNOWN_PUBLISHER_ID:
        return "the publisher is not identified"
    if finding.key in claimed:
        return f"already on {claimed[finding.key]}"
    if finding.incident.publisher_id in blocked:
        return "the publisher's active row has unreadable problems"
    return None


def _update(draft: _Draft, today: date) -> Update | None:
    if draft.after == draft.before:
        return None
    resolved = bool(draft.before) and not draft.after
    body = draft.row.to_body().model_copy(
        update={"problems": draft.after, **_derived(draft.after)}
    )
    if resolved:
        note = f"All problems resolved as of {today.isoformat()}; review and close."
        body = body.model_copy(update={"notes": _append_note(body.notes, note)})
    return Update(
        draft.row,
        body,
        tuple(draft.added),
        draft.refreshed,
        draft.dropped,
        resolved,
    )


def _create(added: Sequence[Finding], today: date, taken: set[str]) -> Create:
    incident = added[0].incident
    problems = [
        problem_payload(f.incident, key=f.key or "", recorded_on=today, rule_id=f.rule.id)
        for f in added
    ]
    body = PublisherNotificationBody(
        id=new_row_id(incident.publisher_id, today, taken),
        publisher_id=incident.publisher_id,
        publisher_name=incident.publisher_name,
        status=NEW_ROW_STATUS,
        notes=f"Created by the daily notification job on {today.isoformat()}.",
        problems=problems,
        **_derived(problems),
    )
    return Create(body, tuple(added))


def new_row_id(publisher_id: str, today: date, taken: set[str]) -> str:
    """`pn_<publisher>-<YYYYMMDD>`, suffixed `-2`, `-3`, … if that id is already used."""
    base = f"pn_{publisher_id.removeprefix('pub_')}-{today:%Y%m%d}"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _derived(problems: Sequence[Any]) -> dict[str, Any]:
    """The row's summary columns, recomputed from its problems."""
    items = [item for item in problems if isinstance(item, dict)]
    monitors = dict.fromkeys(
        m for item in items if isinstance(m := item.get("monitor_id"), str) and m
    )
    ages = [
        d
        for item in items
        if isinstance(d := item.get("days_open"), int) and not isinstance(d, bool)
    ]
    return {
        "problem_count": len(items),
        "monitors": tuple(monitors),
        "oldest_days_open": max(ages) if ages else None,
    }


def _append_note(notes: str | None, note: str) -> str:
    return f"{notes.rstrip()}\n{note}" if notes and notes.strip() else note
