"""The monthly capture end to end, with both API surfaces mocked by respx."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from fixture_loader import load_sample
from stewards.api.models import MetricMeasurement
from stewards.metrics import __main__ as runner

BASE = "https://admin.test"
ENV = {
    "ADMIN_API_BASE_URL": f"{BASE}/",
    "ADMIN_API_TOKEN": "admin-token",
    "ANALYTICS_API_TOKEN": "analytics-token",
}
DECEMBER = datetime(2026, 12, 1, 8, 0, tzinfo=UTC)
NOVEMBER = date(2026, 11, 1)
SNAPSHOTS = f"{BASE}/admin/metric-snapshots"


def mock_api(router: respx.MockRouter, *, missing: str | None = None) -> None:
    """The five reads, plus an empty store. `missing` makes one read answer 404."""
    reads = {
        "quality": (f"{BASE}/admin/feed-quality", "feed_quality_quality"),
        "activity": (f"{BASE}/admin/activity-coverage/summary", "activity_coverage_summary"),
        "summary": (f"{BASE}/admin/summary", "summary"),
        "analytics": (f"{BASE}/summary", "analytics_summary"),
        "growth": (f"{BASE}/admin/ecosystem-growth-trend", "ecosystem_growth"),
    }
    for name, (url, fixture) in reads.items():
        route = router.get(url)
        if name == missing:
            route.respond(404)
        else:
            route.respond(json=load_sample(fixture))
    router.get(SNAPSHOTS).respond(
        json={"data": [], "meta": load_sample("metric_snapshots")["meta"] | {"total": 0}}
    )


def captured(route: respx.Route) -> list[dict[str, object]]:
    return json.loads(route.calls.last.request.content)


def measurement(source: str = "summary", field: str = "feeds") -> MetricMeasurement:
    return MetricMeasurement(period=NOVEMBER, source=source, field=field, value=1)


# --- which month ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 12, 1), date(2026, 11, 1)),
        (date(2026, 12, 31), date(2026, 11, 1)),
        (date(2027, 1, 3), date(2026, 12, 1)),
    ],
)
def test_the_default_period_is_the_month_just_ended(today: date, expected: date) -> None:
    assert runner.previous_month(today) == expected


@pytest.mark.parametrize(
    "period",
    [date(2026, 11, 15), date(2026, 12, 1), date(2027, 1, 1)],
)
def test_a_period_that_cannot_be_captured_is_refused(period: date) -> None:
    """A part-month, the month still running, and one that has not happened."""
    with pytest.raises(runner.PeriodRefused):
        runner.check_period(period, date(2026, 12, 1))


def test_a_finished_month_is_accepted() -> None:
    assert runner.check_period(NOVEMBER, date(2026, 12, 1)) is None


# --- only what is missing -------------------------------------------------------------


def test_measurements_already_stored_are_left_alone() -> None:
    found = [measurement(), measurement(field="datasets")]
    assert runner.unstored(found, [measurement()]) == (measurement(field="datasets"),)


def test_nothing_stored_means_everything_is_sent() -> None:
    found = [measurement(), measurement(field="datasets")]
    assert runner.unstored(found, []) == tuple(found)


def test_a_measurement_of_another_month_does_not_count_as_stored() -> None:
    other = MetricMeasurement(period=date(2026, 10, 1), source="summary", field="feeds")
    assert runner.unstored([measurement()], [other]) == (measurement(),)


# --- a refused write ------------------------------------------------------------------


@respx.mock
def test_a_batch_already_captured_in_full_is_not_a_problem() -> None:
    """What a second run looks like once another has captured the month."""
    respx.post(SNAPSHOTS).respond(409, json={"measurements": ["2026-11-01|summary|feeds"]})
    client = runner.StewardsClient(runner.admin_settings(ENV))
    assert runner.capture(client, [measurement()]) == []


@respx.mock
def test_a_batch_only_part_of_which_clashed_is_a_problem() -> None:
    """The store refuses in full, so the rest went unwritten and need attention."""
    respx.post(SNAPSHOTS).respond(409, json={"measurements": ["2026-11-01|summary|feeds"]})
    client = runner.StewardsClient(runner.admin_settings(ENV))
    problems = runner.capture(client, [measurement(), measurement(field="datasets")])
    assert problems == ["already captured: 2026-11-01|summary|feeds"]


@respx.mock
def test_a_refusal_that_names_nothing_is_reported_as_it_came() -> None:
    respx.post(SNAPSHOTS).respond(409, text="nope")
    client = runner.StewardsClient(runner.admin_settings(ENV))
    assert "conflicting write" in runner.capture(client, [measurement()])[0]


@respx.mock
def test_a_write_that_failed_outright_is_a_problem() -> None:
    respx.post(SNAPSHOTS).respond(503)
    client = runner.StewardsClient(runner.admin_settings(ENV))
    assert runner.capture(client, [measurement()]) != []


# --- the whole run --------------------------------------------------------------------


@respx.mock
def test_a_clean_run_captures_the_month() -> None:
    mock_api(respx.mock)
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert runner.run(ENV, now=DECEMBER) == 0

    sent = captured(write)
    fields = {(row["source"], row["field"]) for row in sent}
    assert all(row["period"] == "2026-11-01" for row in sent)
    assert ("feed_quality", "completeness_location_weighted") in fields
    assert ("feed_quality", "completeness_location_unweighted") in fields
    assert ("activity_coverage", "linked_percentage") in fields
    assert ("summary", "past_threshold_single_feed_stall") in fields
    assert ("analytics_summary", "number_of_facilities") in fields


@respx.mock
def test_each_surface_is_read_with_its_own_token() -> None:
    """They are not interchangeable: the admin token on the public surface is a 401."""
    mock_api(respx.mock)
    respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    runner.run(ENV, now=DECEMBER)

    by_path = {call.request.url.path: call.request.url.params["token"] for call in respx.calls}
    assert by_path["/admin/summary"] == "admin-token"
    assert by_path["/summary"] == "analytics-token"


@respx.mock
def test_a_month_still_running_keeps_its_crawl_figure_out() -> None:
    """November is in progress in this series, so its monthly aggregate is not stored."""
    mock_api(respx.mock)
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    runner.run(ENV, now=DECEMBER)

    assert not any(row["source"] == "ecosystem_growth" for row in captured(write))


@respx.mock
def test_a_finished_month_stores_its_crawl_figure() -> None:
    mock_api(respx.mock)
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert runner.run(ENV, now=DECEMBER, period=date(2026, 10, 1)) == 0

    crawled = [row for row in captured(write) if row["source"] == "ecosystem_growth"]
    assert crawled == [
        {
            "period": "2026-10-01",
            "source": "ecosystem_growth",
            "field": "datasets_crawled",
            "value": 174.0,
            "denominator": 31.0,
            "snapshot_date": "2026-11-13",
        }
    ]


@respx.mock
def test_a_failed_read_costs_its_own_rows_and_nothing_else() -> None:
    mock_api(respx.mock, missing="analytics")
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert runner.run(ENV, now=DECEMBER) == 1

    sources = {row["source"] for row in captured(write)}
    assert "analytics_summary" not in sources
    assert "feed_quality" in sources


@respx.mock
def test_a_month_already_captured_writes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_api(respx.mock)
    respx.get(SNAPSHOTS).respond(
        json={
            "data": [
                {"period": "2026-11-01", "source": source, "field": field}
                for source, field in _every_field()
            ],
            "meta": load_sample("metric_snapshots")["meta"],
        }
    )
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert runner.run(ENV, now=DECEMBER) == 0
    assert not write.called
    assert "0 measurement(s) to capture" in capsys.readouterr().out


def _every_field() -> list[tuple[str, str]]:
    """Every measurement a clean November run would build, read back from a dry run."""
    with respx.mock:
        mock_api(respx.mock)
        collected = runner.collect(
            runner.StewardsClient(runner.admin_settings(ENV)),
            runner.StewardsClient(runner.analytics_settings(ENV)),
            NOVEMBER,
            date(2026, 12, 1),
        )
    return [(m.source, m.field) for m in collected.measurements]


@respx.mock
def test_a_dry_run_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    mock_api(respx.mock)
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert runner.run(ENV, now=DECEMBER, dry_run=True) == 0
    assert not write.called
    assert "(dry run)" in capsys.readouterr().out


@respx.mock
def test_a_store_that_will_not_answer_captures_nothing() -> None:
    """Without knowing what is already there, a write would be refused in full."""
    mock_api(respx.mock)
    respx.get(SNAPSHOTS).respond(503)
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert runner.run(ENV, now=DECEMBER) == 1
    assert not write.called


@respx.mock
def test_the_run_is_written_to_the_step_summary(tmp_path: Path) -> None:
    mock_api(respx.mock)
    respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))
    summary = tmp_path / "summary.md"

    runner.run({**ENV, "GITHUB_STEP_SUMMARY": str(summary)}, now=DECEMBER)

    assert "Metric capture for November 2026" in summary.read_text()


# --- the command line -----------------------------------------------------------------


def test_a_period_that_is_not_a_date_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert runner.main(["--period", "last-month"]) == 1
    assert "error:" in capsys.readouterr().err


def test_a_missing_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner.os, "environ", {"ADMIN_API_BASE_URL": BASE})
    assert runner.main([]) == 1
    assert "ADMIN_API_TOKEN is not set" in capsys.readouterr().err


def test_a_timeout_on_every_read_still_reports_the_month() -> None:
    with respx.mock:
        respx.route().mock(side_effect=httpx.ReadTimeout("slow"))
        assert runner.run(ENV, now=DECEMBER) == 1


# --- the report -----------------------------------------------------------------------


def test_the_report_says_which_measurements_have_no_value() -> None:
    """Null is stored, and the report says so: it reads as not available, not as zero."""
    blank = MetricMeasurement(period=NOVEMBER, source="summary", field="datasets")
    collected = runner.Collected((blank,), ("analytics summary: 404",))

    text = runner.report(
        NOVEMBER, collected, [blank], dry_run=False, problems=["already captured: x"]
    )

    assert "1 measurement(s) have no value" in text
    assert "**Reads that failed**" in text
    assert "**Not captured**" in text
    assert "| summary | datasets | null |" in text
