"""Smoke-test wiring: point the app at mocked live API responses and disable auth."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx

from fixture_loader import load_sample
from stewards import config
from stewards.api import client as client_module
from stewards.api.repository import CUSTOM_PROPERTIES_MONITOR_ID

BASE = "https://api.test/api/v1"

#: Every incident-backed monitor serves incidents; the orphan monitor alone serves no trend.
MONITOR_IDS = (
    "dataset_stall",
    "single_feed_stall",
    "feed_ingestion_error",
    "dataset_orphaned_children",
    "dataset_future_decline",
)

#: Monitors backed by a quality snapshot instead: one read, no incidents and no trend.
QUALITY_IDS = ("feed_quality",)

#: Monitors backed by a coverage snapshot: two reads — the figures and their rows — and no
#: incidents, no trend and no `/summary` entry.
COVERAGE_IDS = ("active_places_coverage",)

#: The custom-property snapshot: one read, rows and a summary block. No monitor is named
#: after it any more — the compliance page joins it with the quality read — but the endpoint
#: still carries that slug, and the repository constant is where it is spelled.
CUSTOM_PROPERTY_IDS = (CUSTOM_PROPERTIES_MONITOR_ID,)

#: Monitors backed by activity coverage: three reads — national figures, the ranking and the
#: district rows — and no incidents, no trend and no `/summary` entry.
ACTIVITY_COVERAGE_IDS = ("activity_coverage",)
TREND_IDS = (
    "dataset_stall",
    "single_feed_stall",
    "feed_ingestion_error",
    "dataset_future_decline",
)


@pytest.fixture(autouse=True)
def smoke_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[respx.MockRouter]:
    """Mock the API responses page smoke tests expect under a live-only config.

    Yields the router, so a test that requests `smoke_env` can ask whether a route was hit.
    Routes worth asking about are named.
    """
    monkeypatch.setenv("STEWARDS_API_BASE_URL", "https://api.test")
    monkeypatch.setenv("STEWARDS_API_TOKEN", "test-token")
    monkeypatch.setenv("STEWARDS_ENV", "dev")
    monkeypatch.setenv("STEWARDS_DISABLE_AUTH", "true")
    config.get_settings.cache_clear()
    client_module.reset_client()

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as mock:
        mock.get(f"{BASE}/summary").mock(
            return_value=httpx.Response(200, json=load_sample("summary"))
        )
        for monitor_id in MONITOR_IDS:
            mock.get(f"{BASE}/monitors/{monitor_id}/incidents").mock(
                return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_incidents"))
            )
        for monitor_id in TREND_IDS:
            mock.get(f"{BASE}/monitors/{monitor_id}/trend").mock(
                return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_trend"))
            )
        # This deployment has not built the orphan trend endpoint, so it 404s — the state
        # the live admin API is actually in. Every page must survive it.
        for monitor_id in set(MONITOR_IDS) - set(TREND_IDS):
            mock.get(f"{BASE}/monitors/{monitor_id}/trend").mock(
                return_value=httpx.Response(404)
            )
        for monitor_id in QUALITY_IDS:
            mock.get(f"{BASE}/monitors/{monitor_id}/quality").mock(
                return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_quality"))
            )
        for monitor_id in COVERAGE_IDS:
            mock.get(f"{BASE}/monitors/{monitor_id}/coverage").mock(
                return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_coverage"))
            )
            mock.get(
                f"{BASE}/monitors/{monitor_id}/mappings", name=f"{monitor_id}_mappings"
            ).mock(return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_mappings")))
        for monitor_id in CUSTOM_PROPERTY_IDS:
            mock.get(f"{BASE}/monitors/{monitor_id}/properties").mock(
                return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_properties"))
            )
        for monitor_id in ACTIVITY_COVERAGE_IDS:
            for resource in ("summary", "top-activities", "districts"):
                fixture = f"{monitor_id}_{resource.replace('-', '_')}"
                mock.get(f"{BASE}/monitors/{monitor_id}/{resource}").mock(
                    return_value=httpx.Response(200, json=load_sample(fixture))
                )
        mock.get(f"{BASE}/ecosystem/growth").mock(
            return_value=httpx.Response(200, json=load_sample("ecosystem_growth"))
        )
        notifications = f"{BASE}/publisher-notifications"
        mock.get(notifications, params={"page": "1"}).mock(
            return_value=httpx.Response(200, json=load_sample("publisher_notifications_page1"))
        )
        mock.get(notifications, params={"page": "2"}).mock(
            return_value=httpx.Response(200, json=load_sample("publisher_notifications_page2"))
        )
        mock.get(f"{notifications}/summary").mock(
            return_value=httpx.Response(
                200, json=load_sample("publisher_notifications_summary")
            )
        )
        yield mock

    client_module.reset_client()
    config.get_settings.cache_clear()
