"""The decision table: every condition at its boundary, and the table's own validation."""

from __future__ import annotations

import pytest

from outreach_factory import dataset_incident, feed_incident, scheduled_feeds
from stewards.api.models import Incident
from stewards.monitors.registry import get_monitor
from stewards.outreach.rules import RULES, AnyItem, AtLeast, OneOf, Rule, monitor_ids, validate

RULE = {rule.id: rule for rule in RULES}
STALL = get_monitor("single_feed_stall")
ORPHANS = get_monitor("dataset_orphaned_children")
DECLINE = get_monitor("dataset_future_decline")


# --- conditions ---------------------------------------------------------------------------


@pytest.mark.parametrize(("days", "holds"), [(9, False), (10, True), (11, True), (None, False)])
def test_at_least_holds_from_the_threshold_up(days: int | None, holds: bool) -> None:
    incident = feed_incident("single_feed_stall", "f1", days)
    assert AtLeast("days_open", 10).holds(STALL, incident) is holds


def test_at_least_ignores_a_boolean_figure() -> None:
    incident = feed_incident("single_feed_stall", "f1", 10, past_threshold=True)
    assert AtLeast("past_threshold", 0).holds(STALL, incident) is False


@pytest.mark.parametrize(("share", "holds"), [(0.19, False), (0.2, True), (None, False)])
def test_at_least_reads_a_detail_figure(share: float | None, holds: bool) -> None:
    incident = dataset_incident("dataset_orphaned_children", orphan_share=share)
    assert AtLeast("detail.orphan_share", 0.2).holds(ORPHANS, incident) is holds


@pytest.mark.parametrize(
    ("feed_type", "holds"),
    [("Slot", True), ("ScheduledSession", True), ("FacilityUse", False), (None, False)],
)
def test_one_of_matches_the_listed_values(feed_type: str | None, holds: bool) -> None:
    incident = feed_incident("single_feed_stall", "f1", 10, feed_type=feed_type)
    condition = OneOf("feed_type", frozenset({"Slot", "ScheduledSession"}))
    assert condition.holds(STALL, incident) is holds


@pytest.mark.parametrize(
    ("feeds", "holds"),
    [
        (scheduled_feeds("SessionSeries", "ScheduledSession"), True),
        (scheduled_feeds("SessionSeries"), False),
        ([], False),
        ([{"feed_id": "f1"}], False),
    ],
)
def test_any_item_needs_one_matching_entry(feeds: list[dict[str, str]], holds: bool) -> None:
    incident = dataset_incident("dataset_future_decline", 15, feeds=feeds)
    condition = AnyItem("detail.feeds", "feed_type", frozenset({"ScheduledSession"}))
    assert condition.holds(DECLINE, incident) is holds


def test_conditions_describe_themselves() -> None:
    assert AtLeast("days_open", 10).describe() == "days_open >= 10"
    assert AtLeast("detail.orphan_share", 0.2).describe() == "detail.orphan_share >= 0.2"
    assert OneOf("feed_type", frozenset({"Slot", "A"})).describe() == "feed_type in {A, Slot}"
    assert (
        AnyItem("detail.feeds", "feed_type", frozenset({"X"})).describe()
        == "any detail.feeds[].feed_type in {X}"
    )
    assert RULE["single_feed_stall"].describe() == (
        "days_open >= 10 and feed_type in {ScheduledSession, Slot}"
    )


# --- the rules as written in the decision tree --------------------------------------------


def _decline(days: int) -> Incident:
    return dataset_incident(
        "dataset_future_decline", days, feeds=scheduled_feeds("ScheduledSession")
    )


@pytest.mark.parametrize(
    ("rule_id", "below", "at"),
    [
        (
            "http_endpoint_failure",
            feed_incident("feed_ingestion_error", "f1", 9),
            feed_incident("feed_ingestion_error", "f1", 10),
        ),
        (
            "dataset_stall",
            dataset_incident("dataset_stall", 4),
            dataset_incident("dataset_stall", 5),
        ),
        (
            "single_feed_stall",
            feed_incident("single_feed_stall", "f1", 9, feed_type="Slot"),
            feed_incident("single_feed_stall", "f1", 10, feed_type="Slot"),
        ),
        (
            "orphaned_children",
            dataset_incident("dataset_orphaned_children", orphan_share=0.1999),
            dataset_incident("dataset_orphaned_children", orphan_share=0.2),
        ),
        ("future_decline", _decline(14), _decline(15)),
    ],
)
def test_each_rule_triggers_at_its_threshold(
    rule_id: str, below: Incident, at: Incident
) -> None:
    assert not RULE[rule_id].triggers(below)
    assert RULE[rule_id].triggers(at)


def test_a_stalled_feed_without_bookable_times_is_not_raised() -> None:
    incident = feed_incident("single_feed_stall", "f1", 30, feed_type="SessionSeries")
    assert not RULE["single_feed_stall"].triggers(incident)


def test_a_decline_with_no_scheduled_session_feed_is_not_raised() -> None:
    incident = dataset_incident(
        "dataset_future_decline", 30, feeds=scheduled_feeds("FacilityUse")
    )
    assert not RULE["future_decline"].triggers(incident)


def test_prechecks_follow_the_decision_tree_order() -> None:
    assert RULE["orphaned_children"].prechecks == ("http_endpoint_failure", "single_feed_stall")
    assert RULE["future_decline"].prechecks == (
        "http_endpoint_failure",
        "dataset_stall",
        "single_feed_stall",
    )


def test_monitor_ids_are_unique_and_in_rule_order() -> None:
    assert monitor_ids(RULES) == (
        "feed_ingestion_error",
        "dataset_stall",
        "single_feed_stall",
        "dataset_orphaned_children",
        "dataset_future_decline",
    )
    assert monitor_ids(()) == ()


# --- validation ---------------------------------------------------------------------------


def _rule(rule_id: str, *prechecks: str, monitor_id: str = "dataset_stall") -> Rule:
    return Rule(rule_id, rule_id, monitor_id, (AtLeast("days_open", 1),), prechecks)


def test_the_shipped_table_is_valid() -> None:
    validate(RULES)
    validate(())


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ((_rule("a"), _rule("a")), "unique"),
        ((_rule("a", monitor_id="nope"),), "unregistered"),
        ((_rule("a", "missing"),), "unknown rule 'missing'"),
        ((_rule("a", "a"),), "unknown rule 'a'"),
        ((_rule("a", "b"), _rule("b", "c"), _rule("c", "a")), "cycle: a -> b -> c -> a"),
    ],
)
def test_validate_rejects_a_table_that_cannot_run(
    rules: tuple[Rule, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate(rules)
