"""The decision tree as data: one `Rule` per notification path. Pure: no I/O, no clock.

A rule names the monitor whose incidents it reads, the conditions an incident must meet
(`when`, all of them), and its pre-checks (`prechecks`): the ids of other rules that take
precedence. If any pre-check rule *triggered* on the same dataset today, this rule's finding
is suppressed — the earlier problem is the one to raise with the publisher, and it probably
explains this one.

Adding a notification path is one entry in `RULES`. A new kind of condition is one small
class with `holds` and `describe`. Field paths use the registry's column syntax
(`days_open`, `detail.orphan_share`), so they reach a monitor's own measurements through its
declared detail model.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from stewards.api.models import Incident
from stewards.monitors.registry import Monitor, get_monitor
from stewards.monitors.transforms import resolve_field


class Condition(Protocol):
    def holds(self, monitor: Monitor, incident: Incident) -> bool: ...

    def describe(self) -> str: ...


@dataclass(frozen=True, slots=True)
class AtLeast:
    """The figure at `field` is a number no smaller than `threshold`.

    `>=` rather than the Slack digest's exact crossing: the notification list remembers
    what it has already raised, so a run that was missed simply catches up the next day.
    A missing figure never holds.
    """

    field: str
    threshold: float

    def holds(self, monitor: Monitor, incident: Incident) -> bool:
        value = resolve_field(monitor, incident, self.field)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return False
        return value >= self.threshold

    def describe(self) -> str:
        return f"{self.field} >= {self.threshold:g}"


@dataclass(frozen=True, slots=True)
class OneOf:
    """The value at `field` is one of `values`, e.g. the incident's feed type."""

    field: str
    values: frozenset[str]

    def holds(self, monitor: Monitor, incident: Incident) -> bool:
        return resolve_field(monitor, incident, self.field) in self.values

    def describe(self) -> str:
        return f"{self.field} in {{{', '.join(sorted(self.values))}}}"


@dataclass(frozen=True, slots=True)
class AnyItem:
    """Some entry of the list at `field` has `item_field` in `values`.

    For a dataset incident that names the feeds behind it, e.g. "one of the declining feeds
    is a ScheduledSession feed". A list the API did not send never holds.
    """

    field: str
    item_field: str
    values: frozenset[str]

    def holds(self, monitor: Monitor, incident: Incident) -> bool:
        items = resolve_field(monitor, incident, self.field) or ()
        return any(getattr(item, self.item_field, None) in self.values for item in items)

    def describe(self) -> str:
        values = ", ".join(sorted(self.values))
        return f"any {self.field}[].{self.item_field} in {{{values}}}"


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    """Stable: it is written onto every problem the rule raises."""

    label: str
    monitor_id: str
    when: tuple[Condition, ...]

    prechecks: tuple[str, ...] = ()
    """Rule ids, in the order the decision tree checks them."""

    def triggers(self, incident: Incident) -> bool:
        monitor = get_monitor(self.monitor_id)
        return all(condition.holds(monitor, incident) for condition in self.when)

    def describe(self) -> str:
        return " and ".join(condition.describe() for condition in self.when)


#: The feed types whose stall a publisher is told about: the ones that carry bookable times.
SCHEDULE_FEED_TYPES = frozenset({"Slot", "ScheduledSession"})

RULES: tuple[Rule, ...] = (
    Rule(
        id="http_endpoint_failure",
        label="HTTP endpoint failure",
        monitor_id="feed_ingestion_error",
        when=(AtLeast("days_open", 10),),
    ),
    Rule(
        id="dataset_stall",
        label="Dataset-wide stall",
        monitor_id="dataset_stall",
        when=(AtLeast("days_open", 5),),
    ),
    Rule(
        id="single_feed_stall",
        label="Single feed stall",
        monitor_id="single_feed_stall",
        when=(AtLeast("days_open", 10), OneOf("feed_type", SCHEDULE_FEED_TYPES)),
    ),
    Rule(
        id="orphaned_children",
        label="Orphaned child items",
        monitor_id="dataset_orphaned_children",
        when=(AtLeast("detail.orphan_share", 0.20),),
        prechecks=("http_endpoint_failure", "single_feed_stall"),
    ),
    Rule(
        id="future_decline",
        label="Decline in future sessions",
        monitor_id="dataset_future_decline",
        # The batch leaves Slot feeds out of this monitor, so of the two schedule types
        # only ScheduledSession can appear here.
        when=(
            AtLeast("days_open", 15),
            AnyItem("detail.feeds", "feed_type", frozenset({"ScheduledSession"})),
        ),
        prechecks=("http_endpoint_failure", "dataset_stall", "single_feed_stall"),
    ),
)


def monitor_ids(rules: Iterable[Rule] = RULES) -> tuple[str, ...]:
    """Every monitor the rules read, once each, in rule order."""
    return tuple(dict.fromkeys(rule.monitor_id for rule in rules))


def validate(rules: Sequence[Rule]) -> None:
    """Raise `ValueError` if the table cannot be run as written.

    Ids are unique, every monitor is registered, every pre-check names another rule, and no
    rules pre-check each other through any chain — that would let each suppress the other,
    and the problem would never be raised at all.
    """
    by_id = {rule.id: rule for rule in rules}
    if len(by_id) != len(rules):
        raise ValueError("Rule ids must be unique")
    for rule in rules:
        try:
            get_monitor(rule.monitor_id)
        except KeyError as exc:
            raise ValueError(f"Rule {rule.id} reads an unregistered monitor") from exc
        for precheck in rule.prechecks:
            if precheck == rule.id or precheck not in by_id:
                raise ValueError(f"Rule {rule.id} pre-checks unknown rule {precheck!r}")

    def visit(rule_id: str, path: tuple[str, ...]) -> None:
        if rule_id in path:
            raise ValueError(f"Pre-check cycle: {' -> '.join((*path, rule_id))}")
        for precheck in by_id[rule_id].prechecks:
            visit(precheck, (*path, rule_id))

    for rule in rules:
        visit(rule.id, ())
