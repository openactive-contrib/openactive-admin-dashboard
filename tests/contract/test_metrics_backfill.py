"""The one-off crawl backfill, with the admin API mocked by respx."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import respx

from fixture_loader import load_sample
from stewards.api.models import EcosystemGrowthResponse, MetricMeasurement
from stewards.metrics import backfill

BASE = "https://admin.test"
ENV = {"ADMIN_API_BASE_URL": f"{BASE}/", "ADMIN_API_TOKEN": "admin-token"}
NOVEMBER = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
GROWTH = f"{BASE}/admin/ecosystem-growth-trend"
SNAPSHOTS = f"{BASE}/admin/metric-snapshots"

#: The fixture runs May to November 2026, with November still in progress.
FINISHED = [date(2026, month, 1) for month in range(5, 11)]


def mock_api(router: respx.MockRouter, stored: list[dict[str, object]] | None = None) -> None:
    router.get(GROWTH).respond(json=load_sample("ecosystem_growth"))
    router.get(SNAPSHOTS).respond(
        json={
            "data": stored or [],
            "meta": load_sample("metric_snapshots")["meta"] | {"total": len(stored or [])},
        }
    )


def held(period: date) -> dict[str, object]:
    return {
        "period": period.isoformat(),
        "source": "ecosystem_growth",
        "field": "datasets_crawled",
    }


def captured(route: respx.Route) -> list[dict[str, object]]:
    return json.loads(route.calls.last.request.content)


# --- which months -----------------------------------------------------------------------


def test_every_finished_month_gets_a_measurement_oldest_first() -> None:
    series = EcosystemGrowthResponse.model_validate(load_sample("ecosystem_growth"))
    rows = backfill.crawl_measurements(series)
    assert [row.period for row in rows] == FINISHED


def test_the_month_still_running_is_left_for_the_monthly_capture() -> None:
    """Its figure covers the days so far and changes daily, and a capture is permanent."""
    series = EcosystemGrowthResponse.model_validate(load_sample("ecosystem_growth"))
    assert date(2026, 11, 1) not in [row.period for row in backfill.crawl_measurements(series)]


def test_an_empty_series_has_nothing_to_recover() -> None:
    series = EcosystemGrowthResponse.model_validate(
        {"data": [], "meta": load_sample("ecosystem_growth")["meta"]}
    )
    assert backfill.crawl_measurements(series) == ()


# --- the whole run ----------------------------------------------------------------------


@respx.mock
def test_a_clean_run_captures_every_finished_month() -> None:
    mock_api(respx.mock)
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert backfill.run(ENV, now=NOVEMBER) == 0

    sent = captured(write)
    assert [row["period"] for row in sent] == [month.isoformat() for month in FINISHED]
    assert all(row["field"] == "datasets_crawled" for row in sent)
    assert sent[-1]["value"] == 174
    assert sent[-1]["denominator"] == 31


@respx.mock
def test_it_reaches_further_back_than_the_growth_page_does() -> None:
    mock_api(respx.mock)
    respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    backfill.run(ENV, now=NOVEMBER)

    growth = next(call for call in respx.calls if call.request.url.path.endswith("-trend"))
    assert growth.request.url.params["months"] == str(backfill.MONTHS)


@respx.mock
def test_months_already_captured_are_left_alone() -> None:
    mock_api(respx.mock, stored=[held(month) for month in FINISHED[:4]])
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert backfill.run(ENV, now=NOVEMBER) == 0
    assert [row["period"] for row in captured(write)] == ["2026-09-01", "2026-10-01"]


@respx.mock
def test_a_second_run_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    mock_api(respx.mock, stored=[held(month) for month in FINISHED])
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert backfill.run(ENV, now=NOVEMBER) == 0
    assert not write.called
    assert "0 month(s) to capture" in capsys.readouterr().out


@respx.mock
def test_a_dry_run_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    mock_api(respx.mock)
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert backfill.run(ENV, now=NOVEMBER, dry_run=True) == 0
    assert not write.called
    assert "(dry run)" in capsys.readouterr().out


@respx.mock
def test_a_series_that_will_not_load_captures_nothing() -> None:
    respx.get(GROWTH).respond(503)
    write = respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))

    assert backfill.run(ENV, now=NOVEMBER) == 1
    assert not write.called


@respx.mock
def test_a_rejected_write_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    mock_api(respx.mock)
    respx.post(SNAPSHOTS).respond(503)

    assert backfill.run(ENV, now=NOVEMBER) == 1
    assert "**Not captured**" in capsys.readouterr().out


@respx.mock
def test_the_run_is_written_to_the_step_summary(tmp_path: Path) -> None:
    mock_api(respx.mock)
    respx.post(SNAPSHOTS).respond(201, json=load_sample("metric_snapshots"))
    summary = tmp_path / "summary.md"

    backfill.run({**ENV, "GITHUB_STEP_SUMMARY": str(summary)}, now=NOVEMBER)

    text = summary.read_text()
    assert "Crawl figure backfill" in text
    assert "| May 2026 |" in text


def test_a_missing_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(backfill.os, "environ", {"ADMIN_API_BASE_URL": BASE})
    assert backfill.main([]) == 1
    assert "ADMIN_API_TOKEN is not set" in capsys.readouterr().err


def test_a_null_crawl_figure_is_reported_as_null() -> None:
    blank = MetricMeasurement(
        period=date(2026, 5, 1), source="ecosystem_growth", field="datasets_crawled"
    )
    text = backfill.report([blank], [blank], dry_run=False)
    assert "| May 2026 | null |  |" in text
