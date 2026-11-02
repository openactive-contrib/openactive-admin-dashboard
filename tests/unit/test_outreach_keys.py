"""Incident identity, and the stored problem reading back the way the page reads it."""

from __future__ import annotations

from datetime import date

from outreach_factory import DATASET, dataset_incident, feed_incident, row
from stewards.api.models import Incident, NotificationProblem
from stewards.monitors.email_draft import draft_publisher_email
from stewards.outreach.keys import dataset_url, incident_key, problem_payload, stored_key

TODAY = date(2026, 10, 6)


def test_a_feed_incident_is_keyed_by_its_feed() -> None:
    incident = feed_incident("single_feed_stall", "feed-9", 10)
    assert incident_key(incident) == "single_feed_stall:feed:feed-9"
    assert dataset_url(incident) == DATASET


def test_a_dataset_incident_is_keyed_by_its_dataset() -> None:
    incident = dataset_incident("dataset_stall", 5)
    assert incident_key(incident) == f"dataset_stall:dataset:{DATASET}"


def test_nothing_to_key_on_gives_none() -> None:
    assert incident_key(dataset_incident("dataset_stall", 5, dataset_url=None)) is None
    assert incident_key(dataset_incident("dataset_stall", 5, dataset_url="  ")) is None


def test_an_unregistered_monitor_has_no_dataset() -> None:
    incident = Incident(
        monitor_id="retired_monitor",
        publisher_id="p",
        publisher_name="P",
        past_threshold=False,
        status="open",
        detail={"dataset_url": DATASET},
    )
    assert dataset_url(incident) is None
    assert incident_key(incident) is None


def test_the_payload_drops_row_owned_fields_and_adds_bookkeeping() -> None:
    incident = feed_incident("single_feed_stall", "f1", 12, last_contacted="2026-09-01")
    key = incident_key(incident)
    assert key is not None
    payload = problem_payload(incident, key=key, recorded_on=TODAY, rule_id="single_feed_stall")

    for field in ("publisher_id", "publisher_name", "status", "past_threshold", "trend"):
        assert field not in payload
    assert "last_contacted" not in payload
    assert payload["incident_key"] == "single_feed_stall:feed:f1"
    assert payload["recorded_on"] == "2026-10-06"
    assert payload["rule_id"] == "single_feed_stall"
    assert payload["detail"]["dataset_url"] == DATASET


def test_a_payload_without_a_rule_has_no_rule_id() -> None:
    incident = dataset_incident("dataset_stall", 5)
    payload = problem_payload(incident, key="k", recorded_on=TODAY, rule_id=None)
    assert "rule_id" not in payload


def test_a_stored_problem_round_trips_to_the_same_key_and_the_email() -> None:
    incident = dataset_incident(
        "dataset_orphaned_children", orphan_share=0.25, orphan_count=50, child_count=200
    )
    key = incident_key(incident)
    assert key is not None
    payload = problem_payload(incident, key=key, recorded_on=TODAY, rule_id="orphaned_children")

    problem = NotificationProblem.model_validate(payload)
    assert problem.rule_id == "orphaned_children"
    assert problem.recorded_on == TODAY
    assert stored_key(problem, "pub_alpha", "Alpha Leisure") == key

    email = draft_publisher_email(row(problems=[payload]), TODAY)
    assert "Alpha" in email
    assert email


def test_a_hand_typed_problem_is_keyed_from_its_fields() -> None:
    problem = NotificationProblem.model_validate(
        {"monitor_id": "single_feed_stall", "feed_id": "f7"}
    )
    assert stored_key(problem, "pub_alpha", "Alpha") == "single_feed_stall:feed:f7"
    assert stored_key(NotificationProblem(monitor_id="dataset_stall"), "p", "P") is None
