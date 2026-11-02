"""The httpx client: auth header, and one typed exception per failure mode.

respx only — nothing here touches the network.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from stewards.api.client import StewardsClient, get_client, reset_client
from stewards.api.endpoints import Style, prefix
from stewards.api.errors import (
    ApiConflict,
    ApiContractError,
    ApiNotFound,
    ApiUnauthorized,
    ApiUnavailable,
)
from stewards.config import Settings

API_PREFIX = prefix(Style.CONTRACT)
BASE = "https://api.test"
URL = f"{BASE}{API_PREFIX}/summary"

LIST_PATH = "/publisher-notifications"
LIST_URL = f"{BASE}{API_PREFIX}{LIST_PATH}"
ROW_PATH = f"{LIST_PATH}/pn_dummy-leisure"
ROW_URL = f"{BASE}{API_PREFIX}{ROW_PATH}"


@pytest.fixture
def client(settings: Settings) -> StewardsClient:
    return StewardsClient(settings)


@respx.mock
def test_happy_path_returns_parsed_json(client: StewardsClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    assert client.get("/summary") == {"ok": True}


@respx.mock
def test_bearer_token_is_sent(client: StewardsClient) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    client.get("/summary")
    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"


@respx.mock
def test_no_auth_header_when_no_token_is_configured() -> None:
    client = StewardsClient(Settings(api_base_url=BASE, api_token=""))
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    client.get("/summary")
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_query_params_are_passed_through(client: StewardsClient) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    client.get("/summary", {"page": 2, "page_size": 500})
    assert dict(route.calls.last.request.url.params) == {"page": "2", "page_size": "500"}


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
def test_rejected_credentials_raise_unauthorized(client: StewardsClient, status: int) -> None:
    respx.get(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(ApiUnauthorized):
        client.get("/summary")


@respx.mock
def test_missing_resource_raises_not_found(client: StewardsClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(404))
    with pytest.raises(ApiNotFound):
        client.get("/summary")


@pytest.mark.parametrize("status", [500, 502, 503])
@respx.mock
def test_server_errors_raise_unavailable(client: StewardsClient, status: int) -> None:
    respx.get(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(ApiUnavailable):
        client.get("/summary")


@respx.mock
def test_a_timeout_raises_unavailable(client: StewardsClient) -> None:
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("too slow"))
    with pytest.raises(ApiUnavailable, match="timed out"):
        client.get("/summary")


@respx.mock
def test_a_connection_failure_raises_unavailable(client: StewardsClient) -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(ApiUnavailable, match="unreachable"):
        client.get("/summary")


@respx.mock
def test_an_unexpected_4xx_is_a_contract_error(client: StewardsClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(422))
    with pytest.raises(ApiContractError):
        client.get("/summary")


@respx.mock
def test_a_non_json_body_is_a_contract_error(client: StewardsClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(ApiContractError, match="non-JSON"):
        client.get("/summary")


@respx.mock
def test_the_token_is_never_in_an_exception_message(client: StewardsClient) -> None:
    respx.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ApiUnavailable) as caught:
        client.get("/summary")
    assert "test-token" not in str(caught.value)


def test_get_client_is_cached_and_resettable(monkeypatch: pytest.MonkeyPatch) -> None:
    from stewards import config

    monkeypatch.setenv("STEWARDS_API_BASE_URL", BASE)
    config.get_settings.cache_clear()
    reset_client()
    try:
        assert get_client() is get_client()
    finally:
        reset_client()
        config.get_settings.cache_clear()


# --- writes -------------------------------------------------------------------------------


@respx.mock
def test_patch_sends_the_body_as_json_and_returns_the_saved_row(
    client: StewardsClient,
) -> None:
    body = {"id": "pn_dummy-leisure", "status": "in_progress"}
    route = respx.patch(ROW_URL).mock(return_value=httpx.Response(200, json={"data": body}))

    assert client.patch(ROW_PATH, body) == {"data": body}

    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == body


@respx.mock
def test_post_sends_the_body_as_json_and_returns_the_created_row(
    client: StewardsClient,
) -> None:
    body = {"id": "pn_dummy-leisure", "status": "open"}
    route = respx.post(LIST_URL).mock(return_value=httpx.Response(201, json={"data": body}))

    assert client.post(LIST_PATH, body) == {"data": body}

    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == body


@respx.mock
def test_delete_accepts_an_empty_204(client: StewardsClient) -> None:
    route = respx.delete(ROW_URL).mock(return_value=httpx.Response(204))

    assert client.delete(ROW_PATH) is None
    assert route.called


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, ApiUnauthorized),
        (403, ApiUnauthorized),
        (404, ApiNotFound),
        (409, ApiConflict),
        (400, ApiContractError),
        (500, ApiUnavailable),
    ],
)
@pytest.mark.parametrize("method", ["post", "patch", "delete"])
@respx.mock
def test_writes_raise_the_same_typed_errors_as_reads(
    client: StewardsClient, method: str, status: int, error: type[Exception]
) -> None:
    url = LIST_URL if method == "post" else ROW_URL
    respx.route(method=method.upper(), url=url).mock(return_value=httpx.Response(status))
    calls = {
        "post": lambda: client.post(LIST_PATH, {"id": "pn_dummy-leisure"}),
        "patch": lambda: client.patch(ROW_PATH, {"id": "pn_dummy-leisure"}),
        "delete": lambda: client.delete(ROW_PATH),
    }
    with pytest.raises(error):
        calls[method]()


@respx.mock
def test_a_write_timeout_raises_unavailable(client: StewardsClient) -> None:
    respx.patch(ROW_URL).mock(side_effect=httpx.ReadTimeout("too_slow"))
    with pytest.raises(ApiUnavailable, match="timed out"):
        client.patch(ROW_PATH, {"id": "pn_dummy-leisure"})


@respx.mock
def test_a_write_body_is_never_in_an_exception_message(client: StewardsClient) -> None:
    respx.patch(ROW_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(ApiUnavailable) as caught:
        client.patch(ROW_PATH, {"contact": "dummy.publisher@example.org"})
    assert "example.org" not in str(caught.value)
