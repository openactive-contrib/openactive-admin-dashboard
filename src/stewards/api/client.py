"""httpx transport for the stewards API. The only module that speaks HTTP.

Returns parsed JSON; turning JSON into models is `repository`'s job. Every failure mode
becomes a typed exception from `errors` so pages can render a distinct message.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from stewards.api.errors import ApiContractError, ApiNotFound, ApiUnauthorized, ApiUnavailable
from stewards.config import Settings, get_settings

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
TIMEOUT = httpx.Timeout(10.0, connect=3.0)
RETRIES = 2


def build_client(
    settings: Settings, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Construct the httpx client. The bearer token is never logged."""
    if transport is None:
        if settings.use_sample_data:
            from stewards.api.sample_transport import sample_transport

            transport = sample_transport()
        else:
            transport = httpx.HTTPTransport(retries=RETRIES)
    headers = {"Accept": "application/json"}
    if settings.api_token:
        headers["Authorization"] = f"Bearer {settings.api_token}"
    return httpx.Client(
        base_url=f"{settings.api_base_url}{API_PREFIX}",
        headers=headers,
        timeout=TIMEOUT,
        transport=transport,
    )


class StewardsClient:
    """Thin GET-only wrapper mapping HTTP outcomes onto typed exceptions."""

    def __init__(
        self, settings: Settings, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._client = build_client(settings, transport)

    @property
    def settings(self) -> Settings:
        return self._settings

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ApiUnavailable(f"The monitoring API timed out on {path}") from exc
        except httpx.TransportError as exc:
            raise ApiUnavailable(f"The monitoring API is unreachable on {path}") from exc

        status = response.status_code
        if status in (401, 403):
            raise ApiUnauthorized("The monitoring API rejected this deployment's token")
        if status == 404:
            raise ApiNotFound(f"No such resource: {path}")
        if status >= 500:
            raise ApiUnavailable(f"The monitoring API returned {status} on {path}")
        if status >= 400:
            raise ApiContractError(f"The monitoring API returned {status} on {path}")

        try:
            return response.json()
        except ValueError as exc:
            log.warning("Non-JSON body from %s (%s bytes)", path, len(response.content))
            raise ApiContractError(
                f"The monitoring API returned a non-JSON body on {path}"
            ) from exc

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
