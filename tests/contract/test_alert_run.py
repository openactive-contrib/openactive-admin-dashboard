"""The alert runner end to end: admin API and Slack webhook mocked with respx."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from fixture_loader import load_sample
from stewards.alerts import __main__ as runner

BASE = "https://admin.test"
WEBHOOK = "https://hooks.test/services/abc"
ENV = {
    "ADMIN_API_BASE_URL": f"{BASE}/",
    "ADMIN_API_TOKEN": "test-token",
    "SLACK_WEBHOOK_URL": WEBHOOK,
}
TUESDAY = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
MONDAY = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
MONITORS = ("dataset_stall", "single_feed_stall", "feed_ingestion_error")


def mock_api(router: respx.MockRouter, *, missing: str | None = None) -> None:
    router.get(f"{BASE}/admin/summary").respond(json=load_sample("admin_summary_partial"))
    for monitor_id in MONITORS:
        route = router.get(f"{BASE}/admin/{monitor_id.replace('_', '-')}-incidents")
        if monitor_id == missing:
            route.respond(404)
        else:
            route.respond(json=load_sample(f"{monitor_id}_incidents"))


def posted(route: respx.Route) -> list[dict[str, object]]:
    return [json.loads(call.request.content) for call in route.calls]


@respx.mock
def test_happy_path_posts_one_digest() -> None:
    mock_api(respx.mock)
    slack_route = respx.post(WEBHOOK).respond(200)

    assert runner.run(ENV, now=TUESDAY) == 0

    [payload] = posted(slack_route)
    body = json.dumps(payload)
    assert "Dataset-wide stalls* (Days stalled = 5): 1" in body
    summary_call = respx.calls[0].request
    assert summary_call.url.params["token"] == "test-token"
    assert summary_call.url.params["as_of"] == "2026-10-06"


@respx.mock
def test_monday_widens_the_window() -> None:
    mock_api(respx.mock)
    slack_route = respx.post(WEBHOOK).respond(200)

    assert runner.run(ENV, now=MONDAY) == 0
    assert "Dataset-wide stalls* (Days stalled = 5): 2" in json.dumps(posted(slack_route))


@respx.mock
def test_one_monitor_missing_still_posts_the_rest() -> None:
    mock_api(respx.mock, missing="feed_ingestion_error")
    slack_route = respx.post(WEBHOOK).respond(200)

    assert runner.run(ENV, now=TUESDAY) == 0
    body = json.dumps(posted(slack_route))
    assert "Feed ingestion errors*: could not be checked" in body
    assert "Dataset-wide stalls" in body


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(401), httpx.TimeoutException("slow")],
    ids=["500", "401", "timeout"],
)
@respx.mock
def test_api_down_posts_alert_and_fails(response: object) -> None:
    route = respx.get(f"{BASE}/admin/summary")
    if isinstance(response, Exception):
        route.side_effect = response
    else:
        route.return_value = response
    slack_route = respx.post(WEBHOOK).respond(200)

    assert runner.run(ENV, now=TUESDAY) == 1
    [payload] = posted(slack_route)
    assert "Admin API is down" in json.dumps(payload)
    assert "test-token" not in json.dumps(payload)


@respx.mock
def test_dry_run_prints_and_posts_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    mock_api(respx.mock)
    env = {k: v for k, v in ENV.items() if k != "SLACK_WEBHOOK_URL"}

    assert runner.run(env, now=TUESDAY, dry_run=True) == 0
    assert '"blocks"' in capsys.readouterr().out


@respx.mock
def test_main_reports_slack_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    mock_api(respx.mock)
    respx.post(WEBHOOK).respond(500)

    assert runner.main([]) == 1


def test_main_reports_missing_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert runner.main(["--dry-run"]) == 1
    assert "ADMIN_API_BASE_URL is not set" in capsys.readouterr().err
