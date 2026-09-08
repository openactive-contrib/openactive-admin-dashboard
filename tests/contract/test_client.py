"""The httpx client: auth header, and one typed exception per failure mode.

respx only — nothing here touches the network.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from stewards.api.client import StewardsClient, get_client, reset_client
from stewards.api.endpoints import Style, prefix
from stewards.api.errors import (
    ApiContractError,
    ApiNotFound,
    ApiUnauthorized,
    ApiUnavailable,
)
from stewards.config import Settings

API_PREFIX = prefix(Style.CONTRACT)
BASE = "https://api.test"
URL = f"{BASE}{API_PREFIX}/summary"


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
