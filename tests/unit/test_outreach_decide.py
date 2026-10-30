"""Triggering and pre-checks: which findings are raised and which are held back."""

from __future__ import annotations

from outreach_factory import dataset_incident, feed_incident, scheduled_feeds
from stewards.api.models import Incident
from stewards.outreach.decide import Finding, Snapshot, decide

OTHER = "https://other.example/opendata"


def snapshot(*incidents: Incident) -> Snapshot:
    grouped: dict[str, list[Incident]] = {}
    for incident in incidents:
        grouped.setdefault(incident.monitor_id, []).append(incident)
    return Snapshot({monitor: tuple(items) for monitor, items in grouped.items()})


def by_rule(findings: tuple[Finding, ...]) -> dict[str, Finding]:
    return {finding.rule.id: finding for finding in findings}


def decline(days: int = 20, **kwargs: object) -> Incident:
    return dataset_incident(
        "dataset_future_decline", days, feeds=scheduled_feeds("ScheduledSession"), **kwargs
    )


def orphans(**kwargs: object) -> Incident:
    return dataset_incident("dataset_orphaned_children", orphan_share=0.5, **kwargs)


def test_nothing_in_nothing_out() -> None:
    assert decide(Snapshot({})) == ()


def test_an_incident_below_its_threshold_is_not_a_finding() -> None:
    assert decide(snapshot(dataset_incident("dataset_stall", 4))) == ()


def test_a_finding_with_no_precheck_firing_is_raised() -> None:
    [finding] = decide(snapshot(decline()))
    assert finding.raised
    assert finding.suppressed_by is None
    assert finding.key == "dataset_future_decline:dataset:https://alpha.example/opendata"


def test_the_first_precheck_in_order_names_the_suppressor() -> None:
    findings = by_rule(
        decide(
            snapshot(
                decline(),
                dataset_incident("dataset_stall", 6),
                feed_incident("feed_ingestion_error", "f1", 12),
            )
        )
    )
    assert findings["future_decline"].suppressed_by == "http_endpoint_failure"
    assert not findings["future_decline"].raised
    assert findings["dataset_stall"].raised
    assert findings["http_endpoint_failure"].raised


def test_a_later_precheck_suppresses_when_the_earlier_did_not_fire() -> None:
    findings = by_rule(
        decide(
            snapshot(orphans(), feed_incident("single_feed_stall", "f2", 10, feed_type="Slot"))
        )
    )
    assert findings["orphaned_children"].suppressed_by == "single_feed_stall"


def test_a_precheck_incident_below_its_own_threshold_does_not_suppress() -> None:
    findings = decide(snapshot(decline(), feed_incident("feed_ingestion_error", "f1", 9)))
    assert [f.rule.id for f in findings] == ["future_decline"]
    assert findings[0].raised


def test_a_precheck_on_another_dataset_does_not_suppress() -> None:
    findings = by_rule(
        decide(snapshot(decline(), dataset_incident("dataset_stall", 6, dataset_url=OTHER)))
    )
    assert findings["future_decline"].raised


def test_a_finding_with_no_dataset_is_never_suppressed() -> None:
    findings = by_rule(
        decide(snapshot(decline(dataset_url=None), dataset_incident("dataset_stall", 6)))
    )
    assert findings["future_decline"].suppressed_by is None
    assert not findings["future_decline"].raised  # nothing to key it on either


def test_a_precheck_with_no_dataset_suppresses_nothing() -> None:
    findings = by_rule(
        decide(
            snapshot(
                orphans(), feed_incident("feed_ingestion_error", "f1", 30, dataset_url=None)
            )
        )
    )
    assert findings["orphaned_children"].raised


def test_the_snapshot_indexes_every_open_incident_by_key() -> None:
    below = dataset_incident("dataset_stall", 1)
    unkeyed = dataset_incident("dataset_stall", 1, dataset_url=None)
    snap = snapshot(below, unkeyed)
    assert list(snap.by_key().values()) == [below]
    assert snap.monitor_ids == frozenset({"dataset_stall"})
