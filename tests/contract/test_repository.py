"""The repository layer: models out, pagination inside, contract errors surfaced.

Every test exercises the plain `_fetch_*` function, which needs no Streamlit runtime.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from stewards.api.client import StewardsClient
from stewards.api.endpoints import Style, prefix
from stewards.api.errors import (
    ApiContractError,
    ApiNotFound,
    ApiUnauthorized,
    ApiUnavailable,
)
from stewards.api.models import IncidentPage, SummaryResponse, TrendResponse
from stewards.api.repository import (
    PAGE_SIZE,
    _fetch_contact_queue,
    _fetch_incidents,
    _fetch_summary,
    _fetch_trend,
)
from stewards.api.sample_transport import load_sample
from stewards.config import Settings

API_PREFIX = prefix(Style.CONTRACT)
BASE = f"https://api.test{API_PREFIX}"
INCIDENTS = f"{BASE}/monitors/single_feed_stall/incidents"


@pytest.fixture
def client(settings: Settings) -> StewardsClient:
    return StewardsClient(settings)


# --- summary ---------------------------------------------------------------------------


@respx.mock
def test_summary_returns_a_model(client: StewardsClient) -> None:
    respx.get(f"{BASE}/summary").mock(
        return_value=httpx.Response(200, json=load_sample("summary"))
    )
    response = _fetch_summary(client)
    assert isinstance(response, SummaryResponse)
    assert response.data.publishers_monitored == 170
    assert response.meta.snapshot_date == date(2026, 8, 21)
    assert response.data.count_for("http_failure").count == 9
    assert response.data.count_for("orphan_children") is None


@respx.mock
def test_summary_with_no_monitors_still_parses(client: StewardsClient) -> None:
    payload = {
        "data": {},
        "meta": {"snapshot_date": "2026-08-21", "generated_at": "2026-08-21T06:12:04Z"},
    }
    respx.get(f"{BASE}/summary").mock(return_value=httpx.Response(200, json=payload))
    assert _fetch_summary(client).data.monitors == ()


@respx.mock
def test_summary_missing_meta_is_a_contract_error(client: StewardsClient) -> None:
    respx.get(f"{BASE}/summary").mock(return_value=httpx.Response(200, json={"data": {}}))
    with pytest.raises(ApiContractError, match="/summary"):
        _fetch_summary(client)


@respx.mock
def test_summary_propagates_transport_failures(client: StewardsClient) -> None:
    respx.get(f"{BASE}/summary").mock(return_value=httpx.Response(503))
    with pytest.raises(ApiUnavailable):
        _fetch_summary(client)


# --- incidents -------------------------------------------------------------------------


@respx.mock
def test_incidents_returns_models_not_dicts(client: StewardsClient) -> None:
    respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=load_sample("single_feed_stall_incidents"))
    )
    page = _fetch_incidents("single_feed_stall", client)
    assert isinstance(page, IncidentPage)
    assert len(page.data) == 23
    incident = page.data[0]
    assert incident.publisher_name == "Freedom Leisure"
    assert incident.first_detected == date(2026, 7, 30)
    assert incident.past_threshold is True


@respx.mock
def test_incidents_requests_a_large_page_size(client: StewardsClient) -> None:
    route = respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=load_sample("single_feed_stall_incidents"))
    )
    _fetch_incidents("single_feed_stall", client)
    assert route.calls.last.request.url.params["page_size"] == str(PAGE_SIZE)


@respx.mock
def test_incidents_pages_until_exhausted(client: StewardsClient, payload) -> None:
    respx.get(INCIDENTS).mock(
        side_effect=[
            httpx.Response(200, json=payload("incidents_page1")),
            httpx.Response(200, json=payload("incidents_page2")),
        ]
    )
    page = _fetch_incidents("single_feed_stall", client)
    assert [i.publisher_name for i in page.data] == ["Publisher A", "Publisher B"]
    assert page.meta.total == 2


@respx.mock
def test_paging_stops_when_a_page_comes_back_empty(client: StewardsClient, payload) -> None:
    """A total that overstates the data must not loop forever."""
    respx.get(INCIDENTS).mock(
        side_effect=[
            httpx.Response(200, json=payload("incidents_page1")),
            httpx.Response(200, json=payload("incidents_empty")),
        ]
    )
    assert len(_fetch_incidents("single_feed_stall", client).data) == 1


@respx.mock
def test_empty_incidents_is_an_empty_page_not_an_error(client: StewardsClient, payload) -> None:
    respx.get(INCIDENTS).mock(return_value=httpx.Response(200, json=payload("incidents_empty")))
    page = _fetch_incidents("single_feed_stall", client)
    assert page.data == ()
    assert page.meta.snapshot_date == date(2026, 8, 21)


@respx.mock
def test_a_payload_missing_a_required_field_is_a_contract_error(
    client: StewardsClient, payload
) -> None:
    respx.get(INCIDENTS).mock(
        return_value=httpx.Response(200, json=payload("incidents_malformed"))
    )
    with pytest.raises(ApiContractError, match="incidents"):
        _fetch_incidents("single_feed_stall", client)


@respx.mock
def test_a_null_optional_field_parses(client: StewardsClient, payload) -> None:
    respx.get(INCIDENTS).mock(return_value=httpx.Response(200, json=payload("incidents_page2")))
    incident = _fetch_incidents("single_feed_stall", client).data[0]
    assert incident.quality_score is None
    assert incident.last_contacted is None
    assert incident.detail == {"last_modified": None}


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, ApiUnauthorized), (403, ApiUnauthorized), (404, ApiNotFound), (500, ApiUnavailable)],
)
@respx.mock
def test_incident_transport_failures_are_typed(
    client: StewardsClient, status: int, expected: type[Exception]
) -> None:
    respx.get(INCIDENTS).mock(return_value=httpx.Response(status))
    with pytest.raises(expected):
        _fetch_incidents("single_feed_stall", client)


@respx.mock
def test_incident_timeout_is_unavailable(client: StewardsClient) -> None:
    respx.get(INCIDENTS).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ApiUnavailable):
        _fetch_incidents("single_feed_stall", client)


# --- trend -----------------------------------------------------------------------------


@respx.mock
def test_trend_returns_thirty_points_and_asks_for_thirty_days(client: StewardsClient) -> None:
    route = respx.get(f"{BASE}/monitors/single_feed_stall/trend").mock(
        return_value=httpx.Response(200, json=load_sample("single_feed_stall_trend"))
    )
    response = _fetch_trend("single_feed_stall", client=client)
    assert isinstance(response, TrendResponse)
    assert len(response.data) == 30
    assert route.calls.last.request.url.params["days"] == "30"


@respx.mock
def test_trend_day_count_is_overridable(client: StewardsClient) -> None:
    route = respx.get(f"{BASE}/monitors/single_feed_stall/trend").mock(
        return_value=httpx.Response(200, json=load_sample("single_feed_stall_trend"))
    )
    _fetch_trend("single_feed_stall", 7, client)
    assert route.calls.last.request.url.params["days"] == "7"


@respx.mock
def test_empty_trend_parses(client: StewardsClient, payload) -> None:
    respx.get(f"{BASE}/monitors/http_failure/trend").mock(
        return_value=httpx.Response(200, json=payload("trend_empty"))
    )
    assert _fetch_trend("http_failure", client=client).data == ()


@respx.mock
def test_malformed_trend_is_a_contract_error(client: StewardsClient) -> None:
    respx.get(f"{BASE}/monitors/http_failure/trend").mock(
        return_value=httpx.Response(200, json={"data": [{"date": "2026-08-21"}], "meta": {}})
    )
    with pytest.raises(ApiContractError):
        _fetch_trend("http_failure", client=client)


# --- contact queue ---------------------------------------------------------------------


@respx.mock
def test_contact_queue_returns_the_cross_monitor_union(client: StewardsClient) -> None:
    respx.get(f"{BASE}/contact-queue").mock(
        return_value=httpx.Response(200, json=load_sample("contact_queue"))
    )
    page = _fetch_contact_queue(client)
    assert len(page.data) == 10
    assert {i.monitor_id for i in page.data} == {"single_feed_stall", "http_failure"}
    assert all(i.past_threshold for i in page.data)


@respx.mock
def test_empty_contact_queue_is_not_an_error(client: StewardsClient, payload) -> None:
    respx.get(f"{BASE}/contact-queue").mock(
        return_value=httpx.Response(200, json=payload("incidents_empty"))
    )
    assert _fetch_contact_queue(client).data == ()


@respx.mock
def test_contact_queue_failure_is_typed(client: StewardsClient) -> None:
    respx.get(f"{BASE}/contact-queue").mock(return_value=httpx.Response(500))
    with pytest.raises(ApiUnavailable):
        _fetch_contact_queue(client)


# --- sample-data parity ----------------------------------------------------------------


def test_the_bundled_payloads_satisfy_the_contract() -> None:
    """The sample transport and the real API must be indistinguishable to the repository."""
    client = StewardsClient(
        Settings(api_base_url="https://sample.invalid", use_sample_data=True)
    )
    assert _fetch_summary(client).data.open_incidents == 32
    assert len(_fetch_incidents("single_feed_stall", client).data) == 23
    assert len(_fetch_incidents("http_failure", client).data) == 9
    assert len(_fetch_trend("http_failure", client=client).data) == 30
    assert len(_fetch_contact_queue(client).data) == 10
