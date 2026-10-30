"""Findings against the notification list: create, append, refresh, drop, and leave alone."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from outreach_factory import dataset_incident, feed_incident, row
from stewards.api.models import Incident, PublisherNotification
from stewards.outreach.decide import Finding, Snapshot, decide
from stewards.outreach.keys import incident_key, problem_payload
from stewards.outreach.reconcile import Create, Plan, Update, new_row_id, reconcile
from stewards.outreach.rules import RULES

TODAY = date(2026, 10, 6)
EARLIER = date(2026, 9, 30)
RULE = {rule.id: rule for rule in RULES}
STALL = dataset_incident("dataset_stall", 6)
STALL_KEY = "dataset_stall:dataset:https://alpha.example/opendata"


def snap(*incidents: Incident) -> Snapshot:
    """Every monitor the rules read is present, as in a real run."""
    grouped: dict[str, list[Incident]] = {rule.monitor_id: [] for rule in RULES}
    for incident in incidents:
        grouped[incident.monitor_id].append(incident)
    return Snapshot({monitor: tuple(items) for monitor, items in grouped.items()})


def plan_for(snapshot: Snapshot, *rows: PublisherNotification) -> Plan:
    return reconcile(snapshot, decide(snapshot), rows, TODAY)


def stored(
    incident: Incident, *, on: date = EARLIER, rule_id: str | None = "r"
) -> dict[str, Any]:
    key = incident_key(incident)
    assert key is not None
    return problem_payload(incident, key=key, recorded_on=on, rule_id=rule_id)


def only(plan: Plan) -> Create | Update:
    [action] = plan.actions
    return action


# --- creating ------------------------------------------------------------------------------


def test_no_findings_and_no_rows_plan_nothing() -> None:
    assert plan_for(snap()) == Plan()


def test_a_new_publisher_gets_a_new_open_row() -> None:
    action = only(plan_for(snap(STALL)))
    assert isinstance(action, Create)
    body = action.body
    assert body.id == "pn_alpha-20261006"
    assert body.status == "open"
    assert body.publisher_id == "pub_alpha"
    assert body.publisher_name == "Alpha Leisure"
    assert body.notes == "Created by the daily notification job on 2026-10-06."
    assert body.problem_count == 1
    assert body.monitors == ("dataset_stall",)
    assert body.oldest_days_open == 6
    assert body.contact is None
    [problem] = body.problems
    assert problem["incident_key"] == STALL_KEY
    assert problem["rule_id"] == "dataset_stall"
    assert problem["recorded_on"] == "2026-10-06"
    assert [f.rule.id for f in action.added] == ["dataset_stall"]


def test_two_findings_for_one_publisher_share_one_new_row() -> None:
    error = feed_incident(
        "feed_ingestion_error", "f1", 12, dataset_url="https://alpha.example/b"
    )
    action = only(plan_for(snap(STALL, error)))
    assert isinstance(action, Create)
    assert action.body.problem_count == 2
    assert action.body.monitors == ("feed_ingestion_error", "dataset_stall")
    assert action.body.oldest_days_open == 12


def test_a_closed_row_is_ignored_and_its_id_is_not_reused() -> None:
    closed = row("pn_alpha-20261006", status="closed", problems=[stored(STALL)])
    action = only(plan_for(snap(STALL), closed))
    assert isinstance(action, Create)
    assert action.body.id == "pn_alpha-20261006-2"


def test_new_row_ids_strip_the_publisher_prefix_and_count_up() -> None:
    assert new_row_id("pub_x", TODAY, set()) == "pn_x-20261006"
    assert new_row_id("x", TODAY, {"pn_x-20261006", "pn_x-20261006-2"}) == "pn_x-20261006-3"


# --- appending and refreshing --------------------------------------------------------------


def test_a_new_finding_is_appended_to_the_active_row() -> None:
    error = feed_incident(
        "feed_ingestion_error", "f1", 12, dataset_url="https://alpha.example/b"
    )
    active = row(problems=[stored(STALL)], notes="Called on Monday.")
    action = only(plan_for(snap(STALL, error), active))
    assert isinstance(action, Update)
    assert action.row is active
    assert [f.rule.id for f in action.added] == ["http_endpoint_failure"]
    assert action.refreshed == 1
    assert action.body.problem_count == 2
    assert action.body.notes == "Called on Monday."
    assert action.body.contact == "someone@alpha.example"
    assert action.body.status == "open"


def test_an_incident_already_on_an_active_row_is_not_raised_again() -> None:
    yesterday = dataset_incident("dataset_stall", 5)
    plan = plan_for(snap(STALL), row(problems=[stored(yesterday, rule_id="dataset_stall")]))
    [skipped] = plan.skipped
    assert skipped.reason == "already on pn_alpha"
    action = only(plan)
    assert isinstance(action, Update)
    assert action.added == ()
    [problem] = action.body.problems
    assert problem["days_open"] == 6
    assert problem["recorded_on"] == EARLIER.isoformat()
    assert problem["rule_id"] == "dataset_stall"


def test_an_unchanged_row_is_not_written() -> None:
    same = stored(STALL, rule_id="dataset_stall")
    plan = plan_for(snap(STALL), row(problems=[same]))
    assert plan.actions == ()
    assert len(plan.skipped) == 1


def test_an_open_incident_below_its_trigger_is_still_refreshed_not_dropped() -> None:
    young = dataset_incident("dataset_stall", 2)
    older = dataset_incident("dataset_stall", 1)
    action = only(plan_for(snap(young), row(problems=[stored(older)])))
    assert isinstance(action, Update)
    assert action.dropped == 0
    assert action.body.problems[0]["days_open"] == 2


def test_new_findings_go_to_the_earliest_active_row() -> None:
    old = row("pn_old", created_at=datetime(2026, 1, 1, tzinfo=UTC), problems=[])
    new = row("pn_new", created_at=datetime(2026, 5, 1, tzinfo=UTC), problems=[])
    undated = row("pn_undated", problems=[])
    action = only(plan_for(snap(STALL), undated, new, old))
    assert isinstance(action, Update)
    assert action.row is old


# --- dropping ------------------------------------------------------------------------------


def test_a_closed_incident_is_dropped_and_a_now_empty_row_is_flagged_once() -> None:
    active = row(problems=[stored(STALL)], notes="Emailed.")
    action = only(plan_for(snap(), active))
    assert isinstance(action, Update)
    assert action.dropped == 1
    assert action.resolved
    assert action.body.problems == []
    assert action.body.problem_count == 0
    assert action.body.monitors == ()
    assert action.body.oldest_days_open is None
    assert (
        action.body.notes
        == "Emailed.\nAll problems resolved as of 2026-10-06; review and close."
    )
    assert action.body.status == "open"

    again = plan_for(snap(), row(problems=[]))
    assert again.actions == ()


def test_the_resolved_note_starts_the_notes_when_there_were_none() -> None:
    action = only(plan_for(snap(), row(problems=[stored(STALL)])))
    assert isinstance(action, Update)
    assert action.body.notes == "All problems resolved as of 2026-10-06; review and close."


def test_a_closed_row_is_never_touched() -> None:
    assert plan_for(snap(), row(status="closed", problems=[stored(STALL)])) == Plan()


# --- leaving alone -------------------------------------------------------------------------


def test_problems_the_job_cannot_recognise_are_kept_verbatim() -> None:
    unmanaged = {"monitor_id": "feed_quality", "feed_id": "q1", "note": "typed by hand"}
    no_key = {"monitor_id": "dataset_stall", "note": "no url"}
    problems = [unmanaged, no_key, "free text", stored(STALL)]
    action = only(plan_for(snap(), row(problems=problems)))
    assert isinstance(action, Update)
    assert action.body.problems == [unmanaged, no_key, "free text"]
    assert action.body.problem_count == 2
    assert action.body.monitors == ("feed_quality", "dataset_stall")
    assert not action.resolved


def test_a_hand_typed_problem_is_recognised_and_refreshed() -> None:
    error = feed_incident("feed_ingestion_error", "f1", 12)
    typed = {"monitor_id": "feed_ingestion_error", "feed_id": "f1", "days_open": 3}
    plan = plan_for(snap(error), row(problems=[typed]))
    assert [s.reason for s in plan.skipped] == ["already on pn_alpha"]
    action = only(plan)
    assert isinstance(action, Update)
    [problem] = action.body.problems
    assert problem["days_open"] == 12
    assert problem["recorded_on"] == TODAY.isoformat()
    assert "rule_id" not in problem


def test_a_row_whose_problems_are_unreadable_holds_back_its_publisher() -> None:
    broken = row(problems={"not": "a list"})
    plan = plan_for(snap(STALL), broken)
    assert plan.actions == ()
    assert plan.unreadable == ("pn_alpha",)
    assert plan.skipped[0].reason == "the publisher's active row has unreadable problems"


def test_a_row_with_null_problems_reads_as_empty() -> None:
    action = only(plan_for(snap(STALL), row(problems=None)))
    assert isinstance(action, Update)
    assert action.body.problem_count == 1


# --- findings that are not raised ----------------------------------------------------------


def test_suppressed_and_unkeyed_findings_are_reported_not_raised() -> None:
    orphans = dataset_incident("dataset_orphaned_children", orphan_share=0.4)
    error = feed_incident("feed_ingestion_error", "f1", 10)
    unkeyed = dataset_incident("dataset_stall", 9, dataset_url=None)
    plan = plan_for(snap(orphans, error, unkeyed))
    reasons = {s.finding.rule.id: s.reason for s in plan.skipped}
    assert reasons == {
        "orphaned_children": "suppressed by http_endpoint_failure on the same dataset",
        "dataset_stall": "no feed id or dataset URL to recognise it by",
    }
    action = only(plan)
    assert action.body.monitors == ("feed_ingestion_error",)


def test_a_finding_for_an_unnamed_publisher_is_not_raised() -> None:
    unnamed = dataset_incident(
        "dataset_stall", 6, publisher_id="pub_unknown", publisher_name=""
    )
    blank = dataset_incident(
        "dataset_stall", 6, dataset_url="https://b.example", publisher_id=""
    )
    plan = plan_for(snap(unnamed, blank))
    assert plan.actions == ()
    assert [s.reason for s in plan.skipped] == ["the publisher is not identified"] * 2


def test_the_same_incident_twice_in_one_run_is_raised_once() -> None:
    finding = Finding(RULE["dataset_stall"], STALL, STALL_KEY, None)
    plan = reconcile(snap(STALL), (finding, finding), (), TODAY)
    assert only(plan).body.problem_count == 1
    assert plan.skipped[0].reason == "already on the new row for Alpha Leisure"


def test_actions_are_ordered_by_publisher_name() -> None:
    def stall(publisher: str, name: str) -> Incident:
        return dataset_incident(
            "dataset_stall",
            6,
            dataset_url=f"https://{publisher}.example",
            publisher_id=f"pub_{publisher}",
            publisher_name=name,
        )

    bravo = row("pn_b", publisher_id="pub_b", publisher_name="Bravo", problems=[])
    plan = plan_for(snap(stall("z", "zed"), stall("b", "Bravo"), STALL), bravo)
    assert [a.publisher_name for a in plan.actions] == ["Alpha Leisure", "Bravo", "zed"]
