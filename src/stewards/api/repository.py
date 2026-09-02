"""Typed reads, one function per endpoint. The only module that imports `client`.

Each endpoint has a plain `_fetch_*` function (importable and callable without a Streamlit
runtime — this is what the tests exercise) and a cached public wrapper. The batch refreshes
once a day, so the cache is deliberately generous and there is no refresh button.
"""

from __future__ import annotations

import logging
from datetime import date

import streamlit as st
from pydantic import BaseModel, ValidationError

from stewards.api import endpoints
from stewards.api.client import StewardsClient, get_client
from stewards.api.errors import ApiContractError
from stewards.api.models import IncidentPage, SummaryResponse, TrendResponse

log = logging.getLogger(__name__)

CACHE_TTL = 3600
PAGE_SIZE = 500
MAX_PAGES = 20


def _parse[T: BaseModel](model: type[T], payload: object, endpoint: str) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        log.warning("Contract mismatch on %s: %s", endpoint, exc.errors(include_url=False))
        raise ApiContractError(
            f"The monitoring API returned an unexpected shape on {endpoint}"
        ) from exc


def _fetch_summary(
    client: StewardsClient | None = None, as_of: date | None = None
) -> SummaryResponse:
    client = client or get_client()
    endpoint = endpoints.summary(client.style, as_of=as_of or date.today())
    return _parse(SummaryResponse, client.get(endpoint.path, endpoint.params), endpoint.path)


def _fetch_incidents(
    monitor_id: str, client: StewardsClient | None = None, as_of: date | None = None
) -> IncidentPage:
    """Fetch every open incident for a monitor, paging inside this function.

    Callers never loop: filtering and searching happen locally over the returned snapshot.
    `as_of` names the snapshot to answer for; it defaults to today, which is the snapshot
    the daily batch has just written.
    """
    client = client or get_client()
    as_of = as_of or date.today()

    def request(page: int) -> endpoints.Endpoint:
        return endpoints.incidents(
            client.style, monitor_id, as_of=as_of, page=page, page_size=PAGE_SIZE
        )

    endpoint = request(1)
    first = _parse(IncidentPage, client.get(endpoint.path, endpoint.params), endpoint.path)

    incidents = list(first.data)
    page = 1
    while len(incidents) < first.meta.total and incidents and page < MAX_PAGES:
        page += 1
        nxt_endpoint = request(page)
        nxt = _parse(
            IncidentPage,
            client.get(nxt_endpoint.path, nxt_endpoint.params),
            nxt_endpoint.path,
        )
        if not nxt.data:
            break
        incidents.extend(nxt.data)
    if len(incidents) < first.meta.total:
        log.warning(
            "Fetched %d of %d incidents for %s", len(incidents), first.meta.total, monitor_id
        )
    return IncidentPage(data=tuple(incidents), meta=first.meta)


def _fetch_trend(
    monitor_id: str,
    days: int = 30,
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> TrendResponse:
    client = client or get_client()
    endpoint = endpoints.trend(client.style, monitor_id, as_of=as_of or date.today(), days=days)
    return _parse(TrendResponse, client.get(endpoint.path, endpoint.params), endpoint.path)


def _fetch_contact_queue(
    client: StewardsClient | None = None, as_of: date | None = None
) -> IncidentPage:
    client = client or get_client()
    endpoint = endpoints.contact_queue(client.style, as_of=as_of or date.today())
    return _parse(IncidentPage, client.get(endpoint.path, endpoint.params), endpoint.path)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading snapshot…")
def fetch_summary() -> SummaryResponse:
    return _fetch_summary()


@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading incidents…")
def fetch_incidents(monitor_id: str) -> IncidentPage:
    return _fetch_incidents(monitor_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_trend(monitor_id: str, days: int = 30) -> TrendResponse:
    return _fetch_trend(monitor_id, days)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading contact queue…")
def fetch_contact_queue() -> IncidentPage:
    return _fetch_contact_queue()
