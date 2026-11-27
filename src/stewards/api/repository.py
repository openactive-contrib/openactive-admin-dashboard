"""Typed reads, one function per endpoint. The only module that imports `client`.

Each endpoint has a plain `_fetch_*` function (importable and callable without a Streamlit
runtime — this is what the tests exercise) and a cached public wrapper. The batch refreshes
once a day, so the cache is deliberately generous and there is no refresh button.

Every cached read is silent (`show_spinner=False`): the cache's own spinner renders at the
top of the page wherever the call happens to sit, so pages wrap their reads in
`components.loading.loading` instead.
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
    ActivityCoverageSummary,
    ActivityCoverageSummaryResponse,
    ActivityDistrict,
    ActivityDistrictPage,
    AnalyticsSummary,
    CoverageResponse,
    CoverageSnapshot,
    CustomPropertyFeed,
    CustomPropertyResponse,
    EcosystemGrowthResponse,
    FeedQualityResponse,
    FeedQualitySummary,
    IncidentPage,
    MetricMeasurement,
    MetricSnapshotPage,
    MetricSnapshotRow,
    PublisherNotification,
    PublisherNotificationBody,
    PublisherNotificationDocument,
    PublisherNotificationPage,
    PublisherNotificationSummaryResponse,
    SchemaComplianceSnapshot,
    SiteMapping,
    SiteMappingPage,
    SummaryResponse,
    TopActivityPage,
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

#: How many activities the national ranking chart shows.
TOP_ACTIVITY_LIMIT = 20


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


def _fetch_analytics_summary(client: StewardsClient | None = None) -> AnalyticsSummary:
    """The public surface's national figures, which is where the facility count lives.

    The API checks this surface against `Api:AccessToken` rather than the `Api:AdminToken`
    the `/admin` routes use, so a deployment that sets the two differently needs a client
    built with the other token. There is no `as_of`: the service reports its latest run and
    dates it with `date`.
    """
    client = client or get_client()
    endpoint = endpoints.analytics_summary(client.style)
    return _parse(AnalyticsSummary, client.get(endpoint.path, endpoint.params), endpoint.path)


def _fetch_incidents(
    monitor_id: str,
    client: StewardsClient | None = None,
    as_of: date | None = None,
    *,
    latest: bool = False,
) -> IncidentPage:
    """Fetch every open incident for a monitor, paging inside this function.

    Callers never loop: filtering and searching happen locally over the returned snapshot.
    `as_of` names the snapshot to answer for; it defaults to today, which is the snapshot
    the daily batch has just written. The admin API echoes a requested `as_of` back as
    `meta.snapshot_date` whether or not that day has landed, so `latest=True` sends no date
    at all and lets `meta.snapshot_date` say which day the batch really has.
    """
    client = client or get_client()
    day = None if latest else (as_of or date.today())

    def request(page: int) -> endpoints.Endpoint:
        return endpoints.incidents(
            client.style, monitor_id, as_of=day, page=page, page_size=PAGE_SIZE
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


def _fetch_activity_summary(
    monitor_id: str, client: StewardsClient | None = None, as_of: date | None = None
) -> ActivityCoverageSummaryResponse:
    """An activity coverage monitor's national figures: one object, no paging."""
    client = client or get_client()
    endpoint = endpoints.activity_summary(client.style, monitor_id, as_of=as_of or date.today())
    payload = client.get(endpoint.path, endpoint.params)
    return _parse(ActivityCoverageSummaryResponse, payload, endpoint.path)


def _fetch_top_activities(
    monitor_id: str,
    limit: int = TOP_ACTIVITY_LIMIT,
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> TopActivityPage:
    """The first `limit` activities of the national ranking, in one request.

    Deliberately not a paging loop: the ranking is the head of a long list, and the rest of
    it is what the per-district rows describe.
    """
    client = client or get_client()
    endpoint = endpoints.top_activities(
        client.style, monitor_id, as_of=as_of or date.today(), limit=limit
    )
    return _parse(TopActivityPage, client.get(endpoint.path, endpoint.params), endpoint.path)


def _fetch_activity_districts(
    monitor_id: str, client: StewardsClient | None = None, as_of: date | None = None
) -> ActivityDistrictPage:
    """Every district-activity row, paging inside this function as `_fetch_site_mappings`
    does: the caller never loops, and the page filters the returned snapshot locally."""
    client = client or get_client()
    as_of = as_of or date.today()

    def request(page: int) -> endpoints.Endpoint:
        return endpoints.activity_districts(
            client.style, monitor_id, as_of=as_of, page=page, page_size=MAPPING_PAGE_SIZE
        )

    endpoint = request(1)
    payload = client.get(endpoint.path, endpoint.params)
    first = _parse(ActivityDistrictPage, payload, endpoint.path)

    rows: list[ActivityDistrict] = list(first.data)
    page = 1
    while len(rows) < first.meta.total and rows and page < MAX_PAGES:
        page += 1
        nxt_endpoint = request(page)
        nxt = _parse(
            ActivityDistrictPage,
            client.get(nxt_endpoint.path, nxt_endpoint.params),
            nxt_endpoint.path,
        )
        if not nxt.data:
            break
        rows.extend(nxt.data)
    if len(rows) < first.meta.total:
        log.warning(
            "Fetched %d of %d activity district rows for %s",
            len(rows),
            first.meta.total,
            monitor_id,
        )
    return ActivityDistrictPage(data=tuple(rows), meta=first.meta)


def _fetch_activity_summaries(
    monitor_ids: Sequence[str],
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> dict[str, ActivityCoverageSummary]:
    """Each activity coverage monitor's national figures, for its overview card.

    Tolerant as the other card reads are: a monitor whose endpoint this deployment has not
    built is left out, and its card reads "not reported" rather than costing the overview.
    """
    summaries: dict[str, ActivityCoverageSummary] = {}
    for monitor_id in monitor_ids:
        try:
            summaries[monitor_id] = _fetch_activity_summary(monitor_id, client, as_of).data
        except ApiError as exc:
            log.info("No activity coverage summary for %s: %s", monitor_id, exc)
    return summaries


def _fetch_custom_properties(
    monitor_id: str, client: StewardsClient | None = None, as_of: date | None = None
) -> CustomPropertyResponse:
    """One monitor's custom-property snapshot: every feed row and the fleet summary block.

    Pages inside this function, as `_fetch_incidents` does, because the rows are paginated.
    The summary block describes the whole fleet whichever page carries it, so the first
    page's copy is kept and later pages contribute rows only.
    """
    client = client or get_client()
    as_of = as_of or date.today()

    def request(page: int) -> endpoints.Endpoint:
        return endpoints.custom_properties(
            client.style, monitor_id, as_of=as_of, page=page, page_size=PAGE_SIZE
        )

    endpoint = request(1)
    first = _parse(
        CustomPropertyResponse, client.get(endpoint.path, endpoint.params), endpoint.path
    )

    feeds: list[CustomPropertyFeed] = list(first.data)
    page = 1
    while len(feeds) < first.meta.total and feeds and page < MAX_PAGES:
        page += 1
        nxt_endpoint = request(page)
        nxt = _parse(
            CustomPropertyResponse,
            client.get(nxt_endpoint.path, nxt_endpoint.params),
            nxt_endpoint.path,
        )
        if not nxt.data:
            break
        feeds.extend(nxt.data)
    if len(feeds) < first.meta.total:
        log.warning(
            "Fetched %d of %d custom-property feeds for %s",
            len(feeds),
            first.meta.total,
            monitor_id,
        )
    return CustomPropertyResponse(data=tuple(feeds), summary=first.summary, meta=first.meta)


# --- schema compliance --------------------------------------------------------------------
#
# No endpoint of its own: the page joins two snapshots the API already serves.

QUALITY_MONITOR_ID = "feed_quality"
CUSTOM_PROPERTIES_MONITOR_ID = "feed_custom_properties"


def _fetch_schema_compliance(
    client: StewardsClient | None = None, as_of: date | None = None
) -> SchemaComplianceSnapshot:
    """The quality and custom-property snapshots, read together for the compliance page.

    The quality read carries the checks themselves, so its failure is the page's failure
    and raises. The custom-property read only adds the non-standard properties, so its
    failure is logged and left as `None` for the page to explain.
    """
    client = client or get_client()
    quality = _fetch_quality(QUALITY_MONITOR_ID, client=client, as_of=as_of)
    try:
        custom = _fetch_custom_properties(
            CUSTOM_PROPERTIES_MONITOR_ID, client=client, as_of=as_of
        )
    except ApiError as exc:
        log.info("No custom-property snapshot for schema compliance: %s", exc)
        custom = None
    return SchemaComplianceSnapshot(quality=quality, custom_properties=custom)


def _fetch_schema_compliance_snapshots(
    monitor_ids: Sequence[str],
    client: StewardsClient | None = None,
    as_of: date | None = None,
) -> dict[str, SchemaComplianceSnapshot]:
    """Each compliance monitor's joined snapshot, for its overview card.

    The monitor id is only the key here, never a path: this read has no endpoint of its own
    and always joins the same two. Tolerant in the same way as `_fetch_quality_summaries` —
    a monitor whose quality endpoint this deployment has not built yet is left out of the
    mapping rather than costing the whole overview.
    """
    snapshots: dict[str, SchemaComplianceSnapshot] = {}
    for monitor_id in monitor_ids:
        try:
            snapshots[monitor_id] = _fetch_schema_compliance(client=client, as_of=as_of)
        except ApiError as exc:
            log.info("No compliance snapshot for %s: %s", monitor_id, exc)
    return snapshots


def _fetch_ecosystem_growth(
    client: StewardsClient | None = None,
    as_of: date | None = None,
    *,
    months: int | None = None,
) -> EcosystemGrowthResponse:
    """The ecosystem's monthly growth series, oldest or newest first as the API sends it.

    No paging loop: one row per calendar month will not reach a page in the life of this
    app. A response that says otherwise is logged rather than silently trimmed.

    `months` reaches further back than the API's default, for the metric backfill.
    """
    client = client or get_client()
    endpoint = endpoints.ecosystem_growth(
        client.style, as_of=as_of or date.today(), months=months
    )
    response = _parse(
        EcosystemGrowthResponse, client.get(endpoint.path, endpoint.params), endpoint.path
    )
    if len(response.data) < response.meta.total:
        log.warning(
            "Fetched %d of %d ecosystem growth periods", len(response.data), response.meta.total
        )
    return response


# --- publisher notifications --------------------------------------------------------------
#
# Stored steward rows, not the daily batch, and the only resource this app writes. Reads are
# cached briefly so a widget click does not query BigQuery, and every write clears them so a
# save is visible on the very next run.

NOTIFICATIONS_TTL = 30


def _fetch_publisher_notifications(
    client: StewardsClient | None = None,
) -> PublisherNotificationPage:
    """Every row on the list, paging inside this function as `_fetch_incidents` does."""
    client = client or get_client()

    def request(page: int) -> endpoints.Endpoint:
        return endpoints.publisher_notifications(client.style, page=page, page_size=PAGE_SIZE)

    endpoint = request(1)
    first = _parse(
        PublisherNotificationPage, client.get(endpoint.path, endpoint.params), endpoint.path
    )

    rows: list[PublisherNotification] = list(first.data)
    page = 1
    while len(rows) < first.meta.total and rows and page < MAX_PAGES:
        page += 1
        nxt_endpoint = request(page)
        nxt = _parse(
            PublisherNotificationPage,
            client.get(nxt_endpoint.path, nxt_endpoint.params),
            nxt_endpoint.path,
        )
        if not nxt.data:
            break
        rows.extend(nxt.data)
    if len(rows) < first.meta.total:
        log.warning("Fetched %d od %d publisher notifications", len(rows), first.meta.total)
    return PublisherNotificationPage(data=tuple(rows), meta=first.meta)


def _fetch_publisher_notification_summary(
    client: StewardsClient | None = None,
) -> PublisherNotificationSummaryResponse:
    client = client or get_client()
    endpoint = endpoints.publisher_notification_summary(client.style)
    return _parse(
        PublisherNotificationSummaryResponse,
        client.get(endpoint.path, endpoint.params),
        endpoint.path,
    )


def _create_publisher_notification(
    body: PublisherNotificationBody, client: StewardsClient | None = None
) -> PublisherNotification:
    """Insert a new row. Only the daily notification job creates rows; the app never does.

    The id is the caller's and must be new: the API answers 409 (`ApiConflict`) otherwise.
    """
    client = client or get_client()
    path = endpoints.publisher_notifications(client.style).path
    payload = client.post(path, body.to_payload())
    return _parse(PublisherNotificationDocument, payload, path).data


def _update_publisher_notification(
    body: PublisherNotificationBody, client: StewardsClient | None = None
) -> PublisherNotification:
    """Save the whole row. The API overwrites every column, so `body` must be complete."""
    client = client or get_client()
    endpoint = endpoints.publisher_notification(client.style, body.id)
    payload = client.patch(endpoint.path, body.to_payload())
    return _parse(PublisherNotificationDocument, payload, endpoint.path).data


def _delete_publisher_notification(
    notification_id: str, client: StewardsClient | None = None
) -> None:
    client = client or get_client()
    endpoint = endpoints.publisher_notification(client.style, notification_id)
    client.delete(endpoint.path)


def _fetch_metric_snapshots(
    client: StewardsClient | None = None,
    *,
    sources: Sequence[str] = (),
    fields: Sequence[str] = (),
    start: date | None = None,
    end: date | None = None,
) -> MetricSnapshotPage:
    """Captured measurements, paging inside this function as the other list reads do.

    No `as_of`: these rows are a history rather than a day's batch, so there is nothing to
    ask a snapshot for. An omitted filter means every value, not none.
    """
    client = client or get_client()

    def request(page: int) -> endpoints.Endpoint:
        return endpoints.metric_snapshots(
            client.style,
            sources=sources,
            fields=fields,
            start=start,
            end=end,
            page=page,
            page_size=PAGE_SIZE,
        )

    endpoint = request(1)
    first = _parse(
        MetricSnapshotPage, client.get(endpoint.path, endpoint.params), endpoint.path
    )

    rows: list[MetricSnapshotRow] = list(first.data)
    page = 1
    while len(rows) < first.meta.total and rows and page < MAX_PAGES:
        page += 1
        nxt_endpoint = request(page)
        nxt = _parse(
            MetricSnapshotPage,
            client.get(nxt_endpoint.path, nxt_endpoint.params),
            nxt_endpoint.path,
        )
        if not nxt.data:
            break
        rows.extend(nxt.data)
    if len(rows) < first.meta.total:
        log.warning("Fetched %d of %d metric snapshots", len(rows), first.meta.total)
    return MetricSnapshotPage(data=tuple(rows), meta=first.meta)


def _capture_metrics(
    measurements: Sequence[MetricMeasurement], client: StewardsClient | None = None
) -> tuple[MetricSnapshotRow, ...]:
    """Capture a batch of measurements, and return them as the store wrote them.

    The API takes the batch whole or refuses it whole: a measurement already captured
    raises `ApiConflict`, and `detail` names which ones. Nothing here retries, splits or
    re-sends — whether a refusal is a harmless retry or a month left unwritten is the
    capture job's judgement to make.
    """
    client = client or get_client()
    path = endpoints.metric_snapshots(client.style).path
    payload = client.post(path, [m.to_payload() for m in measurements])
    return _parse(MetricSnapshotPage, payload, path).data


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_summary() -> SummaryResponse:
    return _fetch_summary()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_incidents(monitor_id: str) -> IncidentPage:
    return _fetch_incidents(monitor_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_trend_points(monitor_id: str, days: int = 30) -> tuple[TrendPoint, ...]:
    return _fetch_trend_points(monitor_id, days)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_monitor_trends(monitor_ids: tuple[str, ...]) -> dict[str, tuple[TrendPoint, ...]]:
    return _fetch_monitor_trends(monitor_ids)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_quality(monitor_id: str) -> FeedQualityResponse:
    return _fetch_quality(monitor_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_quality_summaries(monitor_ids: tuple[str, ...]) -> dict[str, FeedQualitySummary]:
    return _fetch_quality_summaries(monitor_ids)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_coverage(monitor_id: str) -> CoverageResponse:
    return _fetch_coverage(monitor_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_site_mappings(monitor_id: str, rows_id: str) -> SiteMappingPage:
    return _fetch_site_mappings(monitor_id, rows_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_coverage_snapshots(monitor_ids: tuple[str, ...]) -> dict[str, CoverageSnapshot]:
    return _fetch_coverage_snapshots(monitor_ids)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_activity_summary(monitor_id: str) -> ActivityCoverageSummaryResponse:
    return _fetch_activity_summary(monitor_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_top_activities(monitor_id: str, limit: int = TOP_ACTIVITY_LIMIT) -> TopActivityPage:
    return _fetch_top_activities(monitor_id, limit)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_activity_districts(monitor_id: str) -> ActivityDistrictPage:
    return _fetch_activity_districts(monitor_id)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_activity_summaries(
    monitor_ids: tuple[str, ...],
) -> dict[str, ActivityCoverageSummary]:
    return _fetch_activity_summaries(monitor_ids)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_schema_compliance() -> SchemaComplianceSnapshot:
    return _fetch_schema_compliance()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_schema_compliance_snapshots(
    monitor_ids: tuple[str, ...],
) -> dict[str, SchemaComplianceSnapshot]:
    return _fetch_schema_compliance_snapshots(monitor_ids)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_ecosystem_growth() -> EcosystemGrowthResponse:
    return _fetch_ecosystem_growth()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_metric_snapshots() -> MetricSnapshotPage:
    """The whole captured series. Filtering happens locally over it, as everywhere else in
    this app: the series is a few hundred rows a year, and one read serves every view."""
    return _fetch_metric_snapshots()


@st.cache_data(ttl=NOTIFICATIONS_TTL, show_spinner=False)
def fetch_publisher_notifications() -> PublisherNotificationPage:
    return _fetch_publisher_notifications()


@st.cache_data(ttl=NOTIFICATIONS_TTL, show_spinner=False)
def fetch_publisher_notification_summary() -> PublisherNotificationSummaryResponse:
    return _fetch_publisher_notification_summary()


def clear_publisher_notification_cache() -> None:
    """Forget the cached list and counts, so the next run reads what was just written."""
    fetch_publisher_notifications.clear()
    fetch_publisher_notification_summary.clear()


def update_publisher_notification(body: PublisherNotificationBody) -> PublisherNotification:
    saved = _update_publisher_notification(body)
    clear_publisher_notification_cache()
    return saved


def delete_publisher_notification(notification_id: str) -> None:
    _delete_publisher_notification(notification_id)
    clear_publisher_notification_cache()
