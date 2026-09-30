"""The repository layer: models out, pagination inside, contract errors surfaced.

Every test exercises the plain `_fetch_*` function, which needs no Streamlit runtime.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from fixture_loader import load_sample
from stewards.api.client import StewardsClient
from stewards.api.endpoints import Style, prefix
from stewards.api.errors import (
    ApiContractError,
    ApiNotFound,
    ApiUnauthorized,
    ApiUnavailable,
)
from stewards.api.models import (
    CoverageResponse,
    CustomPropertyResponse,
    FeedQualityResponse,
    IncidentPage,
    SiteMappingPage,
    SummaryResponse,
    TrendResponse,
)
from stewards.api.repository import (
    MAPPING_PAGE_SIZE,
    PAGE_SIZE,
    _fetch_contact_queue,
    _fetch_coverage,
    _fetch_coverage_snapshots,
    _fetch_custom_properties,
    _fetch_custom_property_summaries,
    _fetch_incidents,
    _fetch_monitor_trends,
    _fetch_quality,
    _fetch_quality_summaries,
    _fetch_site_mappings,
    _fetch_summary,
    _fetch_trend,
    _fetch_trend_points,
)
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
    assert response.data.count_for("feed_ingestion_error").count == 9
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
    respx.get(f"{BASE}/monitors/feed_ingestion_error/trend").mock(
        return_value=httpx.Response(200, json=payload("trend_empty"))
    )
    assert _fetch_trend("feed_ingestion_error", client=client).data == ()


@respx.mock
def test_malformed_trend_is_a_contract_error(client: StewardsClient) -> None:
    respx.get(f"{BASE}/monitors/feed_ingestion_error/trend").mock(
        return_value=httpx.Response(200, json={"data": [{"date": "2026-08-21"}], "meta": {}})
    )
    with pytest.raises(ApiContractError):
        _fetch_trend("feed_ingestion_error", client=client)


# --- every monitor's trend, for the overview card states -------------------------------


@respx.mock
def test_monitor_trends_returns_a_series_per_monitor(client: StewardsClient) -> None:
    for monitor_id in ("single_feed_stall", "feed_ingestion_error"):
        respx.get(f"{BASE}/monitors/{monitor_id}/trend").mock(
            return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_trend"))
        )
    trends = _fetch_monitor_trends(("single_feed_stall", "feed_ingestion_error"), client)
    assert set(trends) == {"single_feed_stall", "feed_ingestion_error"}
    assert len(trends["single_feed_stall"]) == 30
    assert trends["feed_ingestion_error"][0].open_count == 9


@respx.mock
def test_a_monitor_whose_trend_is_not_deployed_is_left_out(client: StewardsClient) -> None:
    """A registry entry landing before its endpoint must not cost the whole overview."""
    respx.get(f"{BASE}/monitors/single_feed_stall/trend").mock(
        return_value=httpx.Response(200, json=load_sample("single_feed_stall_trend"))
    )
    respx.get(f"{BASE}/monitors/feed_ingestion_error/trend").mock(
        return_value=httpx.Response(404)
    )
    trends = _fetch_monitor_trends(("single_feed_stall", "feed_ingestion_error"), client)
    assert set(trends) == {"single_feed_stall"}


@respx.mock
def test_a_failing_trend_endpoint_does_not_raise(client: StewardsClient) -> None:
    respx.get(f"{BASE}/monitors/single_feed_stall/trend").mock(return_value=httpx.Response(500))
    assert _fetch_monitor_trends(("single_feed_stall",), client) == {}


def test_no_monitors_means_no_requests(client: StewardsClient) -> None:
    assert _fetch_monitor_trends((), client) == {}


# --- contact queue ---------------------------------------------------------------------


@respx.mock
def test_contact_queue_returns_the_cross_monitor_union(client: StewardsClient) -> None:
    respx.get(f"{BASE}/contact-queue").mock(
        return_value=httpx.Response(200, json=load_sample("contact_queue"))
    )
    page = _fetch_contact_queue(client)
    assert len(page.data) == 10
    assert {i.monitor_id for i in page.data} == {"single_feed_stall", "feed_ingestion_error"}
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


# --- a trend endpoint a deployment has not built ------------------------------------------

ORPHAN_TREND = f"{BASE}/monitors/dataset_orphaned_children/trend"


@respx.mock
def test_trend_points_returns_the_series(client: StewardsClient) -> None:
    respx.get(f"{BASE}/monitors/single_feed_stall/trend").mock(
        return_value=httpx.Response(200, json=load_sample("single_feed_stall_trend"))
    )
    points = _fetch_trend_points("single_feed_stall", client=client)
    assert len(points) == 30
    assert points[-1].date == date(2026, 8, 21)


@pytest.mark.parametrize("status", [404, 401, 500])
@respx.mock
def test_a_trend_endpoint_that_is_not_live_is_an_empty_series_not_an_error(
    client: StewardsClient, status: int
) -> None:
    """A monitor page must lose its chart, never its incidents. The overview already did
    this per monitor; this is the same tolerance for the monitor's own page."""
    respx.get(ORPHAN_TREND).mock(return_value=httpx.Response(status))
    assert _fetch_trend_points("dataset_orphaned_children", client=client) == ()


@respx.mock
def test_a_trend_timeout_is_an_empty_series(client: StewardsClient) -> None:
    respx.get(ORPHAN_TREND).mock(side_effect=httpx.ReadTimeout("slow"))
    assert _fetch_trend_points("dataset_orphaned_children", client=client) == ()


@respx.mock
def test_a_malformed_trend_is_an_empty_series_rather_than_a_broken_page(
    client: StewardsClient,
) -> None:
    respx.get(ORPHAN_TREND).mock(
        return_value=httpx.Response(200, json={"data": [{"date": "2026-08-21"}], "meta": {}})
    )
    assert _fetch_trend_points("dataset_orphaned_children", client=client) == ()


# --- the orphan payload's own shape --------------------------------------------------------

ORPHAN_INCIDENTS = f"{BASE}/monitors/dataset_orphaned_children/incidents"


@respx.mock
def test_the_orphan_payload_parses_with_no_incident_age(client: StewardsClient) -> None:
    """This monitor measures a snapshot, so it reports neither field."""
    respx.get(ORPHAN_INCIDENTS).mock(
        return_value=httpx.Response(
            200, json=load_sample("dataset_orphaned_children_incidents")
        )
    )
    page = _fetch_incidents("dataset_orphaned_children", client)
    assert len(page.data) == 7
    assert all(i.days_open is None for i in page.data)
    assert all(i.first_detected is None for i in page.data)
    assert page.meta.snapshot_date == date(2026, 8, 21)


@respx.mock
def test_measurements_beside_the_shared_fields_are_folded_into_detail(
    client: StewardsClient,
) -> None:
    """`extra="ignore"` would drop them, and the table would read empty with no error."""
    payload = {
        "data": [
            {
                "monitor_id": "dataset_orphaned_children",
                "publisher_id": "pub_x",
                "publisher_name": "Publisher X",
                "past_threshold": True,
                "status": "open",
                "orphan_count": 41,
                "dataset_name": "X Sessions",
                "detail": {"by_kind": []},
            }
        ],
        "meta": {"snapshot_date": "2026-08-21", "generated_at": "2026-08-21T06:12:04Z"},
    }
    respx.get(ORPHAN_INCIDENTS).mock(return_value=httpx.Response(200, json=payload))
    incident = _fetch_incidents("dataset_orphaned_children", client).data[0]
    assert incident.detail["orphan_count"] == 41
    assert incident.detail["dataset_name"] == "X Sessions"
    assert incident.detail["by_kind"] == []
    # A declared field stays where it belongs rather than being folded in twice.
    assert "monitor_id" not in incident.detail
    assert incident.monitor_id == "dataset_orphaned_children"


@respx.mock
def test_a_nested_detail_value_wins_over_a_top_level_one_of_the_same_name(
    client: StewardsClient,
) -> None:
    payload = {
        "data": [
            {
                "monitor_id": "dataset_orphaned_children",
                "publisher_id": "pub_x",
                "publisher_name": "Publisher X",
                "past_threshold": False,
                "status": "open",
                "orphan_count": 1,
                "detail": {"orphan_count": 99},
            }
        ],
        "meta": {"snapshot_date": "2026-08-21", "generated_at": "2026-08-21T06:12:04Z"},
    }
    respx.get(ORPHAN_INCIDENTS).mock(return_value=httpx.Response(200, json=payload))
    incident = _fetch_incidents("dataset_orphaned_children", client).data[0]
    assert incident.detail["orphan_count"] == 99


@respx.mock
def test_a_payload_with_nothing_but_the_required_fields_still_parses(
    client: StewardsClient,
) -> None:
    """The fold must not invent a `detail` key where the payload carries no extras."""
    payload = {
        "data": [
            {
                "monitor_id": "dataset_orphaned_children",
                "publisher_id": "pub_x",
                "publisher_name": "Publisher X",
                "past_threshold": False,
                "status": "open",
            }
        ],
        "meta": {"snapshot_date": "2026-08-21", "generated_at": "2026-08-21T06:12:04Z"},
    }
    respx.get(ORPHAN_INCIDENTS).mock(return_value=httpx.Response(200, json=payload))
    assert _fetch_incidents("dataset_orphaned_children", client).data[0].detail == {}


# --- quality -------------------------------------------------------------------------------

QUALITY = f"{BASE}/monitors/feed_quality/quality"


@respx.mock
def test_quality_returns_rows_and_the_summary_block(client: StewardsClient) -> None:
    respx.get(QUALITY).mock(
        return_value=httpx.Response(200, json=load_sample("feed_quality_quality"))
    )
    response = _fetch_quality("feed_quality", client)
    assert isinstance(response, FeedQualityResponse)
    assert response.meta.snapshot_date == date(2026, 8, 21)
    assert len(response.data) == 15
    assert response.summary.total_feeds == 15
    assert response.summary.completeness["location"].feeds_reporting


@respx.mock
def test_quality_rejects_a_payload_of_the_wrong_shape(client: StewardsClient) -> None:
    respx.get(QUALITY).mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(ApiContractError):
        _fetch_quality("feed_quality", client)


@respx.mock
def test_quality_tolerates_a_snapshot_with_no_summary_block(client: StewardsClient) -> None:
    """A batch that sends only rows still renders: the figures read em dash, not zero."""
    meta = load_sample("feed_quality_quality")["meta"]
    respx.get(QUALITY).mock(return_value=httpx.Response(200, json={"data": [], "meta": meta}))
    response = _fetch_quality("feed_quality", client)
    assert response.data == ()
    assert response.summary.average_score is None


@respx.mock
def test_quality_summaries_skip_a_monitor_whose_endpoint_is_not_live(
    client: StewardsClient,
) -> None:
    """The overview must survive an endpoint this deployment has not built, as it does for
    a missing trend."""
    respx.get(QUALITY).mock(return_value=httpx.Response(404))
    assert _fetch_quality_summaries(("feed_quality",), client) == {}


@respx.mock
def test_quality_summaries_return_one_entry_per_monitor(client: StewardsClient) -> None:
    respx.get(QUALITY).mock(
        return_value=httpx.Response(200, json=load_sample("feed_quality_quality"))
    )
    summaries = _fetch_quality_summaries(("feed_quality",), client)
    assert set(summaries) == {"feed_quality"}
    assert summaries["feed_quality"].feeds_with_errors == 3


def test_quality_summaries_of_nothing_is_an_empty_mapping(client: StewardsClient) -> None:
    assert _fetch_quality_summaries((), client) == {}


# --- coverage ------------------------------------------------------------------------------

COVERAGE = f"{BASE}/monitors/active_places_coverage/coverage"
MAPPINGS = f"{BASE}/monitors/active_places_coverage/mappings"
ROWS_ID = "active_places_site_mappings"


@respx.mock
def test_coverage_returns_the_snapshot_figures(client: StewardsClient) -> None:
    respx.get(COVERAGE).mock(
        return_value=httpx.Response(200, json=load_sample("active_places_coverage_coverage"))
    )
    response = _fetch_coverage("active_places_coverage", client)
    assert isinstance(response, CoverageResponse)
    assert response.data.headline.coverage_pct == 26.4
    assert response.data.headline.sites_matched == 7351
    assert len(response.data.coverage_by_region) == 9
    assert response.meta.snapshot_date == date(2026, 8, 21)


@respx.mock
def test_coverage_rejects_a_payload_of_the_wrong_shape(client: StewardsClient) -> None:
    respx.get(COVERAGE).mock(return_value=httpx.Response(200, json={"data": {}}))
    with pytest.raises(ApiContractError):
        _fetch_coverage("active_places_coverage", client)


@respx.mock
def test_coverage_tolerates_a_snapshot_reporting_no_figures(client: StewardsClient) -> None:
    """A batch that computed nothing this run is not a broken contract: the page says so."""
    meta = load_sample("active_places_coverage_coverage")["meta"]
    respx.get(COVERAGE).mock(return_value=httpx.Response(200, json={"data": {}, "meta": meta}))
    response = _fetch_coverage("active_places_coverage", client)
    assert response.data.headline.coverage_pct is None
    assert response.data.coverage_by_region == ()


@respx.mock
def test_site_mappings_follow_every_page(client: StewardsClient) -> None:
    page1, page2 = load_sample("mappings_page1"), load_sample("mappings_page2")
    route = respx.get(MAPPINGS)
    route.side_effect = [
        httpx.Response(200, json=page1),
        httpx.Response(200, json=page2),
    ]
    response = _fetch_site_mappings("active_places_coverage", ROWS_ID, client)
    assert isinstance(response, SiteMappingPage)
    assert len(response.data) == len(page1["data"]) + len(page2["data"])
    assert response.meta.total == page1["meta"]["total"]
    assert route.call_count == 2
    assert route.calls[0].request.url.params["page"] == "1"
    assert route.calls[1].request.url.params["page"] == "2"


@respx.mock
def test_site_mappings_ask_for_the_larger_page(client: StewardsClient) -> None:
    """There are far more pairs than incidents, and the resource caps the page at this."""
    route = respx.get(MAPPINGS).mock(
        return_value=httpx.Response(200, json=load_sample("active_places_coverage_mappings"))
    )
    _fetch_site_mappings("active_places_coverage", ROWS_ID, client)
    assert route.calls[0].request.url.params["page_size"] == str(MAPPING_PAGE_SIZE)
    assert MAPPING_PAGE_SIZE > PAGE_SIZE


@respx.mock
def test_site_mappings_stop_rather_than_loop_on_an_empty_page(
    client: StewardsClient,
) -> None:
    """A `total` the rows never reach must end the loop, not spin to `MAX_PAGES`."""
    first = load_sample("mappings_page1")
    first["meta"] = {**first["meta"], "total": 10_000}
    route = respx.get(MAPPINGS)
    route.side_effect = [
        httpx.Response(200, json=first),
        httpx.Response(200, json={"data": [], "meta": first["meta"]}),
    ]
    response = _fetch_site_mappings("active_places_coverage", ROWS_ID, client)
    assert len(response.data) == len(first["data"])
    assert route.call_count == 2


@respx.mock
def test_site_mappings_return_an_empty_page_rather_than_raising(
    client: StewardsClient,
) -> None:
    meta = load_sample("active_places_coverage_mappings")["meta"]
    respx.get(MAPPINGS).mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {**meta, "total": 0}})
    )
    assert _fetch_site_mappings("active_places_coverage", ROWS_ID, client).data == ()


@respx.mock
def test_coverage_snapshots_skip_a_monitor_whose_endpoint_is_not_live(
    client: StewardsClient,
) -> None:
    """One undeployed endpoint must not cost the whole overview."""
    respx.get(COVERAGE).mock(return_value=httpx.Response(404))
    assert _fetch_coverage_snapshots(("active_places_coverage",), client) == {}


@respx.mock
def test_coverage_snapshots_return_one_entry_per_monitor(client: StewardsClient) -> None:
    respx.get(COVERAGE).mock(
        return_value=httpx.Response(200, json=load_sample("active_places_coverage_coverage"))
    )
    snapshots = _fetch_coverage_snapshots(("active_places_coverage",), client)
    assert set(snapshots) == {"active_places_coverage"}
    assert snapshots["active_places_coverage"].headline.coverage_pct == 26.4


# --- custom properties ---------------------------------------------------------------------

PROPERTIES = f"{BASE}/monitors/feed_custom_properties/properties"
DRIFT_ID = "feed_custom_properties"


def _drift_page(page: int, rows: slice, total: int) -> dict[str, object]:
    """One page of the sample snapshot. Every page carries the summary block, as the live
    endpoint does."""
    sample = load_sample("feed_custom_properties_properties")
    meta = {**sample["meta"], "page": page, "page_size": PAGE_SIZE, "total": total}
    return {"data": sample["data"][rows], "summary": sample["summary"], "meta": meta}


@respx.mock
def test_custom_properties_return_rows_and_the_summary_block(client: StewardsClient) -> None:
    route = respx.get(PROPERTIES).mock(
        return_value=httpx.Response(200, json=load_sample("feed_custom_properties_properties"))
    )
    response = _fetch_custom_properties(DRIFT_ID, client)
    assert isinstance(response, CustomPropertyResponse)
    assert response.meta.snapshot_date == date(2026, 8, 21)
    assert len(response.data) == 12
    assert response.summary.datasets_with_custom_properties == 62
    assert response.summary.distinct_custom_properties == 57
    assert route.call_count == 1
    assert route.calls.last.request.url.params["page_size"] == str(PAGE_SIZE)


@respx.mock
def test_custom_properties_follow_the_total_across_pages(client: StewardsClient) -> None:
    """The rows page; the summary describes the whole fleet, so the first page's is kept."""
    first, second = _drift_page(1, slice(0, 7), 12), _drift_page(2, slice(7, None), 12)
    second["summary"] = {}  # a later page's summary is not read
    route = respx.get(PROPERTIES).mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
    )
    response = _fetch_custom_properties(DRIFT_ID, client)
    assert len(response.data) == 12
    assert route.call_count == 2
    assert route.calls.last.request.url.params["page"] == "2"
    assert response.summary.feeds_with_custom_properties == 161


@respx.mock
def test_custom_properties_stop_on_an_empty_page_short_of_the_total(
    client: StewardsClient, caplog: pytest.LogCaptureFixture
) -> None:
    first, empty = _drift_page(1, slice(0, 7), 20), _drift_page(2, slice(0, 0), 20)
    respx.get(PROPERTIES).mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=empty)]
    )
    response = _fetch_custom_properties(DRIFT_ID, client)
    assert len(response.data) == 7
    assert "Fetched 7 of 20" in caplog.text


@respx.mock
def test_custom_properties_reject_a_payload_of_the_wrong_shape(client: StewardsClient) -> None:
    respx.get(PROPERTIES).mock(return_value=httpx.Response(200, json={"data": []}))
    with pytest.raises(ApiContractError):
        _fetch_custom_properties(DRIFT_ID, client)


@respx.mock
def test_custom_properties_tolerate_a_snapshot_with_no_summary_block(
    client: StewardsClient,
) -> None:
    """A batch that sends only rows still renders: the figures read em dash, not zero."""
    meta = load_sample("feed_custom_properties_properties")["meta"]
    respx.get(PROPERTIES).mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {**meta, "total": 0}})
    )
    response = _fetch_custom_properties(DRIFT_ID, client)
    assert response.data == ()
    assert response.summary.datasets_with_custom_properties is None


@respx.mock
def test_custom_property_summaries_skip_a_monitor_whose_endpoint_is_not_live(
    client: StewardsClient,
) -> None:
    respx.get(PROPERTIES).mock(return_value=httpx.Response(404))
    assert _fetch_custom_property_summaries((DRIFT_ID,), client) == {}


@respx.mock
def test_custom_property_summaries_return_one_entry_per_monitor(
    client: StewardsClient,
) -> None:
    respx.get(PROPERTIES).mock(
        return_value=httpx.Response(200, json=load_sample("feed_custom_properties_properties"))
    )
    summaries = _fetch_custom_property_summaries((DRIFT_ID,), client)
    assert set(summaries) == {DRIFT_ID}
    assert summaries[DRIFT_ID].publishers_with_custom_properties == 61


def test_custom_property_summaries_of_nothing_is_an_empty_mapping(
    client: StewardsClient,
) -> None:
    assert _fetch_custom_property_summaries((), client) == {}
