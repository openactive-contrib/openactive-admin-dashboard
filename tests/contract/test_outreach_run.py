"""The notification job end to end: the admin API mocked with respx, nothing on the network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from stewards.outreach import __main__ as runner
from stewards.outreach.rules import RULES, monitor_ids

BASE = "https://admin.test"
ENV = {"ADMIN_API_BASE_URL": f"{BASE}/", "ADMIN_API_TOKEN": "test-token"}
NOW = datetime(2026, 10, 6, 7, 7, tzinfo=UTC)  # 08:07 in London
TODAY = "2026-10-06"
LIST = f"{BASE}/admin/publisher-notifications"
CONTACT = "ops@bravo.example"

STALL = {
    "monitor_id": "dataset_stall",
    "publisher_id": "pub_alpha",
    "publisher_name": "Alpha Leisure",
    "first_detected": "2026-09-30",
    "days_open": 6,
    "past_threshold": True,
    "status": "open",
    "detail": {"dataset_url": "https://alpha.example/data", "dataset_name": "Alpha data"},
}
ERROR = {
    "monitor_id": "feed_ingestion_error",
    "publisher_id": "pub_bravo",
    "publisher_name": "Bravo Sport",
    "first_detected": "2026-09-24",
    "days_open": 12,
    "past_threshold": True,
    "status": "open",
    "feed_id": "bravo-sessions",
    "feed_name": "sessions",
    "feed_type": "ScheduledSession",
    "feed_url": "https://bravo.example/data/sessions",
    "dataset_url": "https://bravo.example/data",
    "dataset_name": "Bravo data",
    "error_code": "HTTP_503",
}
BRAVO_ROW = {
    "id": "pn_bravo",
    "publisher_id": "pub_bravo",
    "publisher_name": "Bravo Sport",
    "status": "in_progress",
    "contact": CONTACT,
    "notes": "Phoned.",
    "problems": [],
}


def page(data: list[dict[str, Any]], *, snapshot: str = TODAY, total: int | None = None) -> Any:
    meta = {
        "snapshot_date": snapshot,
        "generated_at": f"{snapshot}T06:00:00Z",
        "page": 1,
        "page_size": 500,
        "total": len(data) if total is None else total,
    }
    return {"data": data, "meta": meta}


def mock_api(
    router: respx.MockRouter,
    *,
    snapshot: str = TODAY,
    failing: str | None = None,
    short: str | None = None,
    rows: list[dict[str, Any]] | None = None,
    rows_total: int | None = None,
) -> None:
    incidents = {"dataset_stall": [STALL], "feed_ingestion_error": [ERROR]}
    for monitor_id in monitor_ids(RULES):
        route = router.get(f"{BASE}/admin/{monitor_id.replace('_', '-')}-incidents")
        data = incidents.get(monitor_id, [])
        if monitor_id == failing:
            route.respond(500)
        elif monitor_id == short:
            route.respond(json=page(data, total=len(data) + 1))
        else:
            route.respond(json=page(data, snapshot=snapshot))
    listed = [BRAVO_ROW] if rows is None else rows
    router.get(LIST).respond(json=page(listed, total=rows_total))


def echo(status: int) -> Any:
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=page_doc(json.loads(request.content)))

    return reply


def page_doc(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": body,
        "meta": {"snapshot_date": TODAY, "generated_at": f"{TODAY}T07:07:00Z"},
    }


def sent(route: respx.Route) -> list[dict[str, Any]]:
    return [json.loads(call.request.content) for call in route.calls]


@respx.mock
def test_a_run_creates_one_row_and_appends_to_another(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    mock_api(respx.mock)
    post = respx.post(LIST).mock(side_effect=echo(201))
    patch = respx.patch(f"{LIST}/pn_bravo").mock(side_effect=echo(200))
    summary = tmp_path / "summary.md"

    assert runner.run({**ENV, "GITHUB_STEP_SUMMARY": str(summary)}, now=NOW) == 0

    [created] = sent(post)
    assert created["id"] == "pn_alpha-20261006"
    assert created["status"] == "open"
    assert created["problems"][0]["rule_id"] == "dataset_stall"
    [updated] = sent(patch)
    assert updated["status"] == "in_progress"
    assert updated["contact"] == CONTACT
    assert updated["notes"] == "Phoned."
    assert updated["problem_count"] == 1
    assert updated["problems"][0]["incident_key"] == "feed_ingestion_error:feed:bravo-sessions"

    reads = [call.request for call in respx.calls if call.request.method == "GET"]
    incident_reads = [r for r in reads if r.url.path.endswith("-incidents")]
    assert all("as_of" not in r.url.params for r in incident_reads)
    assert all(r.url.params["token"] == "test-token" for r in reads)

    report = summary.read_text()
    assert "1 row(s) to create, 1 to update" in report
    assert CONTACT not in report
    assert CONTACT not in capsys.readouterr().out


@respx.mock
def test_a_dry_run_writes_nothing_and_hides_contacts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_api(respx.mock)
    post = respx.post(LIST)
    patch = respx.patch(f"{LIST}/pn_bravo")

    assert runner.run(ENV, now=NOW, dry_run=True) == 0

    assert not post.called
    assert not patch.called
    out = capsys.readouterr().out
    assert "(dry run)" in out
    assert "pn_alpha-20261006" in out
    assert CONTACT not in out


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ({"snapshot": "2026-10-05"}, "snapshot is 2026-10-05, not 2026-10-06"),
        ({"failing": "dataset_stall"}, "dataset_stall:"),
        ({"short": "single_feed_stall"}, "single_feed_stall: read 0 of 1 incidents"),
        ({"rows_total": 2}, "publisher notifications: read 1 of 2 rows"),
    ],
)
@respx.mock
def test_a_failed_gate_writes_nothing(
    setup: dict[str, Any],
    message: str,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("stewards.api.repository.MAX_PAGES", 1)
    mock_api(respx.mock, **setup)
    post = respx.post(LIST)
    patch = respx.patch(f"{LIST}/pn_bravo")
    summary = tmp_path / "summary.md"

    assert runner.run({**ENV, "GITHUB_STEP_SUMMARY": str(summary)}, now=NOW) == 1

    assert not post.called
    assert not patch.called
    assert message in capsys.readouterr().err
    assert "Nothing written" in summary.read_text()


@respx.mock
def test_an_unreadable_list_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    mock_api(respx.mock)
    respx.get(LIST).respond(500)
    post = respx.post(LIST)

    assert runner.run(ENV, now=NOW) == 1
    assert not post.called
    assert "publisher notifications:" in capsys.readouterr().err


@respx.mock
def test_a_rejected_write_fails_the_run_but_not_the_others(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_api(respx.mock)
    respx.post(LIST).respond(409)
    patch = respx.patch(f"{LIST}/pn_bravo").mock(side_effect=echo(200))

    assert runner.run(ENV, now=NOW) == 1

    assert patch.called
    out = capsys.readouterr().out
    assert "Failed writes" in out
    assert "pn_alpha-20261006 (Alpha Leisure)" in out


@respx.mock
def test_a_quiet_day_writes_nothing() -> None:
    for monitor_id in monitor_ids(RULES):
        respx.get(f"{BASE}/admin/{monitor_id.replace('_', '-')}-incidents").respond(
            json=page([])
        )
    respx.get(LIST).respond(json=page([]))
    post = respx.post(LIST)

    assert runner.run(ENV, now=NOW) == 0
    assert not post.called


@respx.mock
def test_the_report_lists_findings_held_back(capsys: pytest.CaptureFixture[str]) -> None:
    held = {**ERROR, "days_open": 13}
    mock_api(respx.mock)
    respx.get(f"{BASE}/admin/feed-ingestion-error-incidents").respond(json=page([ERROR, held]))
    resolved_row = {
        **BRAVO_ROW,
        "id": "pn_old",
        "publisher_id": "pub_old",
        "problems": [
            {"monitor_id": "dataset_stall", "incident_key": "dataset_stall:dataset:gone"}
        ],
    }
    respx.get(LIST).respond(
        json=page(
            [
                BRAVO_ROW,
                resolved_row,
                {**BRAVO_ROW, "id": "pn_bad", "publisher_id": "pub_bad", "problems": {"x": 1}},
            ]
        )
    )

    assert runner.run(ENV, now=NOW, dry_run=True) == 0

    out = capsys.readouterr().out
    assert "already on pn_bravo" in out
    assert "update, all resolved" in out
    assert "Left alone, problems unreadable: pn_bad" in out


def test_main_reports_missing_configuration(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADMIN_API_BASE_URL", raising=False)
    assert runner.main([]) == 1
    assert "ADMIN_API_BASE_URL is not set" in capsys.readouterr().err
