"""Typed reads, one function per endpoint. The only module that imports `client`.

Each endpoint has a plain `_fetch_*` function (importable and callable without a Streamlit
runtime — this is what the tests exercise) and a cached public wrapper. The batch refreshes
once a day, so the cache is deliberately generous and there is no refresh button.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date

import streamlit as st
from pydantic import BaseModel, ValidationError

from stewards.api import endpoints
from stewards.api.client import StewardsClient, get_client
from stewards.api.errors import ApiContractError, ApiError
from stewards.api.models import (
    CoverageResponse,
    CoverageSnapshot,
    FeedQualityResponse,
    FeedQualitySummary,
    IncidentPage,
    SiteMapping,
    SiteMappingPage,
    SummaryResponse,
    TrendPoint,
    TrendResponse,
)

log = logging.getLogger(__name__)

CACHE_TTL = 3600
PAGE_SIZE = 500
MAX_PAGES = 20

#: Mapping rows are far more numerous than incidents and the resource caps the page at this,
#: so asking for more would silently get this anyway. Ten thousand pairs is eleven requests,
#: comfortably inside `MAX_PAGES`.
MAPPING_PAGE_SIZE = 1000


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


def _fetch_trend_points(
    monitor_id: str,
    days: int = 30,
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> tuple[TrendPoint, ...]:
    """One monitor's daily series, empty when this deployment does not serve it.

    The same tolerance `_fetch_monitor_trends` applies for the overview, for the monitor's
    own page: a trend endpoint that is not live costs that page its chart, never its
    incidents. A monitor page that could not load its *incidents* still fails loudly.
    """
    try:
        return _fetch_trend(monitor_id, days, client=client, as_of=as_of).data
    except ApiError as exc:
        log.info("No trend series for %s: %s", monitor_id, exc)
        return ()


def _fetch_monitor_trends(
    monitor_ids: Sequence[str],
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> dict[str, tuple[TrendPoint, ...]]:
    """Each monitor's daily series, for the overview card states and the sidebar badges.

    A monitor whose trend endpoint this deployment has not built yet is left out of the
    mapping rather than raising: the overview judges it on the sparkline in `/summary`
    instead, and one missing endpoint must not cost the whole page.
    """
    trends: dict[str, tuple[TrendPoint, ...]] = {}
    for monitor_id in monitor_ids:
        points = _fetch_trend_points(monitor_id, client=client, as_of=as_of)
        if points:
            trends[monitor_id] = points
    return trends


def _fetch_quality(
    monitor_id: str, client: StewardsClient | None = None, as_of: date | None = None
) -> FeedQualityResponse:
    """One monitor's whole quality snapshot: its feed rows and its fleet summary block.

    No paging loop: this read answers with the fleet in one response, and the summary block
    beside the rows describes that same fleet — a second page would leave the two disagreeing.
    """
    client = client or get_client()
    endpoint = endpoints.quality(client.style, monitor_id, as_of=as_of or date.today())
    return _parse(
        FeedQualityResponse, client.get(endpoint.path, endpoint.params), endpoint.path
    )


def _fetch_quality_summaries(
    monitor_ids: Sequence[str],
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> dict[str, FeedQualitySummary]:
    """Each quality monitor's summary block, for its overview card.

    Tolerant in the same way as `_fetch_monitor_trends`: a monitor whose quality endpoint
    this deployment has not built yet is left out of the mapping, and its card says the
    figures were not reported rather than costing the whole overview.
    """
    summaries: dict[str, FeedQualitySummary] = {}
    for monitor_id in monitor_ids:
        try:
            response = _fetch_quality(monitor_id, client=client, as_of=as_of)
            summaries[monitor_id] = response.summary
        except ApiError as exc:
            log.info("No quality snapshot for %s: %s", monitor_id, exc)
    return summaries


def _fetch_coverage(
    monitor_id: str, client: StewardsClient | None = None, as_of: date | None = None
) -> CoverageResponse:
    """One monitor's coverage snapshot: this run's figures for the whole estate.

    No paging loop, as with the quality snapshot: this read answers with one object, and the
    rows it describes are a separate resource — see `_fetch_site_mappings`.
    """
    client = client or get_client()
    endpoint = endpoints.coverage(client.style, monitor_id, as_of=as_of or date.today())
    return _parse(CoverageResponse, client.get(endpoint.path, endpoint.params), endpoint.path)


def _fetch_site_mappings(
    monitor_id: str,
    rows_id: str,
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> SiteMappingPage:
    """Every site-venue pair behind a coverage snapshot, paging inside this function.

    The same bargain `_fetch_incidents` makes: the caller never loops, and filtering,
    searching and sorting then happen locally over the returned snapshot.
    """
    client = client or get_client()
    as_of = as_of or date.today()

    def request(page: int) -> endpoints.Endpoint:
        return endpoints.coverage_mappings(
            client.style,
            monitor_id,
            rows_id,
            as_of=as_of,
            page=page,
            page_size=MAPPING_PAGE_SIZE,
        )

    endpoint = request(1)
    first = _parse(SiteMappingPage, client.get(endpoint.path, endpoint.params), endpoint.path)

    mappings: list[SiteMapping] = list(first.data)
    page = 1
    while len(mappings) < first.meta.total and mappings and page < MAX_PAGES:
        page += 1
        nxt_endpoint = request(page)
        nxt = _parse(
            SiteMappingPage,
            client.get(nxt_endpoint.path, nxt_endpoint.params),
            nxt_endpoint.path,
        )
        if not nxt.data:
            break
        mappings.extend(nxt.data)
    if len(mappings) < first.meta.total:
        log.warning(
            "Fetched %d of %d site mappings for %s",
            len(mappings),
            first.meta.total,
            monitor_id,
        )
    return SiteMappingPage(data=tuple(mappings), meta=first.meta)


def _fetch_coverage_snapshots(
    monitor_ids: Sequence[str],
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> dict[str, CoverageSnapshot]:
    """Each coverage monitor's snapshot, for its overview card.

    Tolerant in the same way as `_fetch_quality_summaries`: a monitor whose coverage endpoint
    this deployment has not built yet is left out of the mapping rather than costing the
    whole overview. The rows are not read here — the card states figures, not pairs.
    """
    snapshots: dict[str, CoverageSnapshot] = {}
    for monitor_id in monitor_ids:
        try:
            snapshots[monitor_id] = _fetch_coverage(monitor_id, client=client, as_of=as_of).data
        except ApiError as exc:
            log.info("No coverage snapshot for %s: %s", monitor_id, exc)
    return snapshots


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
def fetch_trend_points(monitor_id: str, days: int = 30) -> tuple[TrendPoint, ...]:
    return _fetch_trend_points(monitor_id, days)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_monitor_trends(monitor_ids: tuple[str, ...]) -> dict[str, tuple[TrendPoint, ...]]:
    return _fetch_monitor_trends(monitor_ids)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading quality snapshot…")
def fetch_quality(monitor_id: str) -> FeedQualityResponse:
    return _fetch_quality(monitor_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_quality_summaries(monitor_ids: tuple[str, ...]) -> dict[str, FeedQualitySummary]:
    return _fetch_quality_summaries(monitor_ids)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading coverage snapshot…")
def fetch_coverage(monitor_id: str) -> CoverageResponse:
    return _fetch_coverage(monitor_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading site mappings…")
def fetch_site_mappings(monitor_id: str, rows_id: str) -> SiteMappingPage:
    return _fetch_site_mappings(monitor_id, rows_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_coverage_snapshots(monitor_ids: tuple[str, ...]) -> dict[str, CoverageSnapshot]:
    return _fetch_coverage_snapshots(monitor_ids)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading contact queue…")
def fetch_contact_queue() -> IncidentPage:
    return _fetch_contact_queue()
