"""Endpoint routing: one logical read, two URL shapes.

Pure routing, so these are unit tests. The wire behaviour that follows from them — the
token as a query parameter, the `as_of` snapshot, a missing endpoint raising before any
request — is in `tests/contract/test_admin_api.py`.
"""

from __future__ import annotations

from datetime import date

import pytest

from stewards.api import endpoints
from stewards.api.endpoints import Style

AS_OF = date(2026, 9, 2)


# --- prefix and slug ----------------------------------------------------------------------


def test_the_contract_shape_is_versioned_and_the_admin_shape_sits_at_the_root() -> None:
    assert endpoints.prefix(Style.CONTRACT) == "/api/v1"
    assert endpoints.prefix(Style.ADMIN) == ""


@pytest.mark.parametrize(
    ("monitor_id", "expected"),
    [
        ("single_feed_stall", "single-feed-stall"),
        ("http_failure", "http-failure"),
        ("zerofuture", "zerofuture"),
    ],
)
def test_the_admin_shape_spells_a_monitor_id_with_hyphens(
    monitor_id: str, expected: str
) -> None:
    assert endpoints.monitor_slug(monitor_id) == expected


# --- incidents ----------------------------------------------------------------------------


def test_contract_incidents_path_and_paging_query() -> None:
    endpoint = endpoints.incidents(
        Style.CONTRACT, "single_feed_stall", as_of=AS_OF, page=2, page_size=500
    )
    assert endpoint.path == "/monitors/single_feed_stall/incidents"
    assert endpoint.params == {"page": 2, "page_size": 500}
    assert "as_of" not in endpoint.params  # the contract endpoint takes no snapshot date


def test_admin_incidents_path_carries_the_snapshot_and_the_paging_query() -> None:
    endpoint = endpoints.incidents(
        Style.ADMIN, "single_feed_stall", as_of=AS_OF, page=1, page_size=500
    )
    assert endpoint.path == "/admin/single-feed-stall-incidents"
    assert endpoint.params == {"as_of": "2026-09-02", "page": 1, "page_size": 500}


def test_incidents_paging_defaults_to_the_first_page() -> None:
    for style in Style:
        assert endpoints.incidents(style, "http_failure", as_of=AS_OF).params["page"] == 1


# --- trend --------------------------------------------------------------------------------


def test_contract_trend_asks_for_a_window() -> None:
    endpoint = endpoints.trend(Style.CONTRACT, "single_feed_stall", as_of=AS_OF, days=30)
    assert endpoint.path == "/monitors/single_feed_stall/trend"
    assert endpoint.params == {"days": 30}


def test_admin_trend_is_singular_and_takes_the_snapshot_not_a_window() -> None:
    """The admin API decides the window itself; sending `days` would mean nothing."""
    endpoint = endpoints.trend(Style.ADMIN, "single_feed_stall", as_of=AS_OF, days=30)
    assert endpoint.path == "/admin/single-feed-stall-trend"
    assert endpoint.params == {"as_of": "2026-09-02"}


# --- endpoints a shape does not have ------------------------------------------------------


def test_contract_summary_and_queue_are_unqualified() -> None:
    assert endpoints.summary(Style.CONTRACT, as_of=AS_OF) == endpoints.Endpoint("/summary")
    assert endpoints.contact_queue(Style.CONTRACT, as_of=AS_OF) == endpoints.Endpoint(
        "/contact-queue"
    )


def test_admin_summary_and_queue_sit_under_admin_and_name_the_snapshot() -> None:
    assert endpoints.summary(Style.ADMIN, as_of=AS_OF) == endpoints.Endpoint(
        "/admin/summary", {"as_of": "2026-09-02"}
    )
    assert endpoints.contact_queue(Style.ADMIN, as_of=AS_OF) == endpoints.Endpoint(
        "/admin/contact-queue", {"as_of": "2026-09-02"}
    )


def test_every_style_routes_all_four_reads() -> None:
    """A shape that cannot answer one of these silently loses a page."""
    for style in Style:
        assert endpoints.incidents(style, "single_feed_stall", as_of=AS_OF).path
        assert endpoints.trend(style, "single_feed_stall", as_of=AS_OF).path
        assert endpoints.summary(style, as_of=AS_OF).path
        assert endpoints.contact_queue(style, as_of=AS_OF).path
