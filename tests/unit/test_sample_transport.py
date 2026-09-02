"""Path routing in the bundled-payload transport.

The payload files are named for the contract shape, and `Settings.effective_api_style`
guarantees sample-data mode asks for that shape whatever `STEWARDS_API_STYLE` says — so
this is the only shape the transport routes.
"""

from __future__ import annotations

import pytest

from stewards.api.sample_transport import _resolve


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/v1/summary", "summary"),
        ("/api/v1/contact-queue", "contact_queue"),
        ("/api/v1/monitors/single_feed_stall/incidents", "single_feed_stall_incidents"),
        ("/api/v1/monitors/single_feed_stall/trend", "single_feed_stall_trend"),
        ("/api/v1/monitors/http_failure/incidents", "http_failure_incidents"),
    ],
)
def test_every_bundled_payload_is_reachable(path: str, expected: str) -> None:
    assert _resolve(path) == expected


@pytest.mark.parametrize(
    "path",
    ["/", "/api/v1/nope", "/admin/single-feed-stall-incidents", "/api/v1/monitors/x/detail"],
)
def test_an_unroutable_path_resolves_to_nothing(path: str) -> None:
    """The handler turns this into a 404, which surfaces as ApiNotFound on the page."""
    assert _resolve(path) is None
