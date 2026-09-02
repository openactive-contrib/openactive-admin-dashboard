"""The interim admin API: per-monitor paths, a snapshot date, and a token in the query.

The payloads under `tests/fixtures/admin_*` are the shape the deployed admin endpoints
actually return, including their `open` status token and a row whose optional fields are
null. respx only — nothing here touches the network, and no real token appears in this
suite.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from stewards.api.client import StewardsClient
from stewards.api.endpoints import Style
from stewards.api.errors import ApiNotFound
from stewards.api.repository import (
    _fetch_contact_queue,
    _fetch_incidents,
    _fetch_summary,
    _fetch_trend,
)
from stewards.api.sample_transport import load_sample
from stewards.config import Settings
from stewards.monitors.registry import SINGLE_FEED_STALL
from stewards.monitors.transforms import to_dataframe

BASE = "http://localhost:5268"
INCIDENTS = f"{BASE}/admin/single-feed-stall-incidents"
TRENDS = f"{BASE}/admin/single-feed-stall-trend"
AS_OF = date(2026, 9, 2)


@pytest.fixture
def admin_settings() -> Settings:
    return Settings(
        api_base_url=BASE,
        api_token="test-token",
        api_style=Style.ADMIN,
        api_token_param="token",
    )


@pytest.fixture
def client(admin_settings: Settings) -> StewardsClient:
    return StewardsClient(admin_settings)


# --- the two endpoints that exist ---------------------------------------------------------


@respx.mock
def test_incidents_come_from_the_per_monitor_admin_path(
    client: StewardsClient, payload
) -> None:
    respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=payload("admin_single_feed_stall_incidents"))
    )
    page = _fetch_incidents("single_feed_stall", client, as_of=AS_OF)

    assert page.meta.snapshot_date == AS_OF
    assert len(page.data) == 4
    first = page.data[0]
    assert first.publisher_name == "Actihire"
    assert first.days_open == 13
    assert first.status == "open"
    assert first.detail == {"last_modified": "2026-08-20"}


@respx.mock
def test_a_gap_in_the_series_and_a_fractional_score_still_parse(
    client: StewardsClient, payload
) -> None:
    """Both are in the live payload: every row's series ends null, and 7 scores are floats."""
    respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=payload("admin_single_feed_stall_incidents"))
    )
    last = _fetch_incidents("single_feed_stall", client, as_of=AS_OF).data[-1]

    assert last.trend == (1.0, 2.0, None, 4.0, 5.0)
    assert last.quality_score == 72.1


@respx.mock
def test_the_request_names_the_snapshot_and_the_page(client: StewardsClient, payload) -> None:
    route = respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=payload("admin_single_feed_stall_incidents"))
    )
    _fetch_incidents("single_feed_stall", client, as_of=AS_OF)

    params = dict(route.calls.last.request.url.params)
    assert params["as_of"] == "2026-09-02"
    assert params["page"] == "1"
    assert params["page_size"] == "500"


@respx.mock
def test_the_snapshot_defaults_to_today(client: StewardsClient, payload) -> None:
    """The daily batch writes today's snapshot, so that is what a page asks for."""
    route = respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=payload("admin_single_feed_stall_incidents"))
    )
    _fetch_incidents("single_feed_stall", client)
    assert route.calls.last.request.url.params["as_of"] == date.today().isoformat()


@respx.mock
def test_the_token_travels_as_a_query_parameter_not_a_header(
    client: StewardsClient, payload
) -> None:
    route = respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=payload("admin_single_feed_stall_incidents"))
    )
    _fetch_incidents("single_feed_stall", client, as_of=AS_OF)

    request = route.calls.last.request
    assert request.url.params["token"] == "test-token"
    assert "Authorization" not in request.headers


@respx.mock
def test_the_trend_comes_from_the_singular_path_with_no_window(
    client: StewardsClient, payload
) -> None:
    route = respx.get(TRENDS).mock(
        return_value=httpx.Response(200, json=payload("admin_single_feed_stall_trend"))
    )
    trend = _fetch_trend("single_feed_stall", client=client, as_of=AS_OF)

    assert [point.open_count for point in trend.data][-1] == 129
    params = dict(route.calls.last.request.url.params)
    assert params["as_of"] == "2026-09-02"
    assert "days" not in params


@respx.mock
def test_the_real_payload_fills_every_column_the_registry_declares(
    client: StewardsClient, payload
) -> None:
    """Regression guard on the live shape: a renamed detail key would empty a column."""
    respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=payload("admin_single_feed_stall_incidents"))
    )
    page = _fetch_incidents("single_feed_stall", client, as_of=AS_OF)
    frame = to_dataframe(SINGLE_FEED_STALL, page.data)

    assert list(frame.columns) == [col.label for col in SINGLE_FEED_STALL.columns]
    assert frame["Last modified"].tolist() == ["2026-08-20", "2026-08-26", "—", "2026-08-28"]
    assert frame["Days stalled"].tolist() == ["13d", "7d", "1d", "5d"]
    assert set(frame["Status"]) == {"Open"}
    # The null point is dropped: LineChartColumn needs numbers, or the cell renders as text.
    assert frame["30d trend"].tolist()[-1] == [1.0, 2.0, 4.0, 5.0]


# --- the fleet-wide endpoints -------------------------------------------------------------


@respx.mock
def test_the_summary_comes_from_the_admin_root_and_names_the_snapshot(
    client: StewardsClient,
) -> None:
    """The payload shape is the same in both shapes; only the path and query differ."""
    route = respx.get(f"{BASE}/admin/summary").mock(
        return_value=httpx.Response(200, json=load_sample("summary"))
    )
    summary = _fetch_summary(client, as_of=AS_OF)

    assert summary.data.publishers_monitored == 170
    assert route.calls.last.request.url.params["as_of"] == "2026-09-02"


@respx.mock
def test_the_live_summary_reports_only_the_counts_it_computes(
    client: StewardsClient, payload
) -> None:
    """Regression: the deployed `/admin/summary` sends null for the counts it does not
    compute yet. A null count is not an integer, and it is not a zero either.
    """
    respx.get(f"{BASE}/admin/summary").mock(
        return_value=httpx.Response(200, json=payload("admin_summary_partial"))
    )
    summary = _fetch_summary(client, as_of=AS_OF).data

    assert summary.publishers_monitored == 179
    assert summary.publishers_with_issues == 0
    assert summary.feeds == 463
    assert summary.open_incidents is None
    assert summary.past_threshold is None
    stalls = summary.count_for("single_feed_stall")
    assert stalls is not None
    assert (stalls.count, stalls.past_threshold_count) == (126, 119)


@respx.mock
def test_the_contact_queue_comes_from_the_admin_root(client: StewardsClient) -> None:
    route = respx.get(f"{BASE}/admin/contact-queue").mock(
        return_value=httpx.Response(200, json=load_sample("contact_queue"))
    )
    page = _fetch_contact_queue(client, as_of=AS_OF)

    assert page.data
    assert route.calls.last.request.url.params["token"] == "test-token"


# --- endpoints that are not deployed yet --------------------------------------------------


@respx.mock
def test_an_endpoint_that_is_not_deployed_yet_surfaces_as_not_found(
    client: StewardsClient,
) -> None:
    """What every unbuilt endpoint does today: the server 404s, the page says so."""
    for path in ("/admin/http-failure-incidents", "/admin/contact-queue"):
        respx.get(f"{BASE}{path}").mock(
            return_value=httpx.Response(404, json={"error": "unknown report"})
        )
    with pytest.raises(ApiNotFound):
        _fetch_incidents("http_failure", client, as_of=AS_OF)
    with pytest.raises(ApiNotFound):
        _fetch_contact_queue(client, as_of=AS_OF)
