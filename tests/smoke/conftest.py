"""Smoke-test wiring: point the app at mocked live API responses and disable auth."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx

from fixture_loader import load_sample
from stewards import config
from stewards.api import client as client_module

BASE = "https://api.test/api/v1"


@pytest.fixture(autouse=True)
def smoke_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Mock the API responses page smoke tests expect under a live-only config."""
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
        mock.get(f"{BASE}/contact-queue").mock(
            return_value=httpx.Response(200, json=load_sample("contact_queue"))
        )
        for monitor_id in ("single_feed_stall", "http_failure"):
            mock.get(f"{BASE}/monitors/{monitor_id}/incidents").mock(
                return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_incidents"))
            )
            mock.get(f"{BASE}/monitors/{monitor_id}/trend").mock(
                return_value=httpx.Response(200, json=load_sample(f"{monitor_id}_trend"))
            )
        yield

    client_module.reset_client()
    config.get_settings.cache_clear()
