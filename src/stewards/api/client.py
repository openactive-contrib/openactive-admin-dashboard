"""httpx transport for the stewards API. The only module that speaks HTTP.

Returns parsed JSON; turning JSON into models is `repository`'s job. Every failure mode
becomes a typed exception from `errors` so pages can render a distinct message.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from stewards.api import endpoints
from stewards.api.errors import (
    ApiConflict,
    ApiContractError,
    ApiNotFound,
    ApiUnauthorized,
    ApiUnavailable,
)
from stewards.config import Settings, get_settings

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, connect=3.0)
RETRIES = 2

#: What a write sends: one resource, or a batch of them. A notification row is one object;
#: a monthly metric capture is a flat array that the API takes or refuses as a whole.
WriteBody = Mapping[str, Any] | Sequence[Mapping[str, Any]]

#: The statuses the API answers with an error body worth keeping. A refused capture names
#: the measurements it refused, which is the difference between a harmless retry and a
#: batch that went unwritten.
DETAILED = (400, 409)


def build_client(
    settings: Settings,
    transport: httpx.BaseTransport | None = None,
    timeout: httpx.Timeout = TIMEOUT,
) -> httpx.Client:
    """Construct the httpx client. The token is never logged.

    The path prefix follows the configured API shape, and the token travels in the header
    unless `api_token_param` names a query parameter to carry it instead.
    """
    if transport is None:
        transport = httpx.HTTPTransport(retries=RETRIES)
    headers = {"Accept": "application/json"}
    if settings.api_token and not settings.api_token_param:
        headers["Authorization"] = f"Bearer {settings.api_token}"
    return httpx.Client(
        base_url=f"{settings.api_base_url}{endpoints.prefix(settings.api_style)}",
        headers=headers,
        timeout=timeout,
        transport=transport,
    )


class StewardsClient:
    """Thin wrapper mapping HTTP outcomes onto typed exceptions.

    Reads are GET. Two resources are written to and no others: publisher notifications,
    which the app PATCHes and DELETEs and the daily notification job (`stewards.outreach`)
    POSTs and PATCHes, and the metric snapshot store, which the monthly capture POSTs a
    batch of measurements to. Everything else is read-only.
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout = TIMEOUT,
    ) -> None:
        self._settings = settings
        self._client = build_client(settings, transport, timeout)

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def style(self) -> endpoints.Style:
        """Which URL shape requests are built for."""
        return self._settings.api_style

    def _query(self, params: dict[str, Any] | None) -> dict[str, Any] | None:
        """The caller's query, plus the token when this API takes it as a parameter.

        Only ever passed to httpx as `params`, never formatted into a log line or an error
        message: every message below names the path, which carries no query string.
        """
        token_param = self._settings.api_token_param
        if not (token_param and self._settings.api_token):
            return params
        return {**(params or {}), token_param: self._settings.api_token}

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Send one request and turn every failure into a typed `ApiError`.

        Never logs `params` or `json`: the first can carry the token, and a write body can
        carry a contact address. Every message names the path only.
        """
        try:
            response = self._client.request(method, path, params=self._query(params), json=json)
        except httpx.TimeoutException as exc:
            raise ApiUnavailable(f"The monitoring API timed out on {path}") from exc
        except httpx.TransportError as exc:
            raise ApiUnavailable(f"The monitoring API is unreachable on {path}") from exc

        status = response.status_code
        if status in (401, 403):
            raise ApiUnauthorized("The monitoring API rejected this deployment's token")
        if status == 404:
            raise ApiNotFound(f"No such resource: {path}")
        if status == 409:
            raise ApiConflict(
                f"The monitoring API refused a conflicting write on {path}",
                self._detail(response),
            )
        if status >= 500:
            raise ApiUnavailable(f"The monitoringAPI returned {status} on {path}")
        if status >= 400:
            raise ApiContractError(
                f"The monitoring API returned {status} on {path}", self._detail(response)
            )
        return response

    @staticmethod
    def _detail(response: httpx.Response) -> Any:
        """The API's own error body, or None if it did not send a readable one.

        Only read on the statuses the contract documents, and never put in the message: a
        caller that can act on it reads `ApiError.detail`.
        """
        if response.status_code not in DETAILED:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _json(response: httpx.Response, path: str) -> Any:
        """The parsed body, or a contract error if it is not JSON."""
        try:
            return response.json()
        except ValueError as exc:
            log.warning("Non-JSON body from %s (%s bytes)", path, len(response.content))
            raise ApiContractError(
                f"The monitoring API returned a non-JSON body on {path}"
            ) from exc

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._json(self._send("GET", path, params=params), path)

    def post(self, path: str, body: WriteBody) -> Any:
        """Create from `body` — one resource, or a batch of them — and return it as stored.

        A batch is sent as the flat array the API expects, and is taken or refused whole.
        """
        return self._json(self._send("POST", path, json=body), path)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        """Send `body` as the whole replacement for a resource, and return its new state."""
        return self._json(self._send("PATCH", path, json=body), path)

    def delete(self, path: str) -> None:
        """Remove a resource. The API answers 204 with no body, so nothing is parsed."""
        self._send("DELETE", path)

    def close(self) -> None:
        self._client.close()


_client: StewardsClient | None = None


def get_client() -> StewardsClient:
    """Process-wide client. Streamlit reruns the script, so never build one per rerun."""
    global _client
    if _client is None:
        _client = StewardsClient(get_settings())
    return _client


def reset_client() -> None:
    """Drop the cached client (used by tests and by settings changes)."""
    global _client
    if _client is not None:
        _client.close()
    _client = None
