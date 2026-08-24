"""Shared fixtures. No test touches the network, a real token, or BigQuery.

Happy-path payloads are the ones bundled in `stewards/api/sample_data` — the app serves the
same files in sample-data mode, so there is one copy of each contract shape. The variants a
test needs and the app does not (empty, malformed, paginated) live in `tests/fixtures`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from stewards import config
from stewards.api.models import IncidentPage, SummaryResponse, TrendResponse
from stewards.api.sample_transport import load_sample
from stewards.config import Settings
from stewards.monitors.registry import HTTP_FAILURE, SINGLE_FEED_STALL, Monitor

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SNAPSHOT_DATE = date(2026, 8, 21)


@pytest.fixture(autouse=True)
def _ignore_developer_secrets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep a local `.streamlit/secrets.toml` out of the suite.

    `get_settings` layers the `[stewards]` section of secrets.toml under the environment,
    so without this a developer's own file decides what the tests see — and the placeholder
    `api_token` from secrets.toml.example is a non-ASCII character that httpx refuses to
    put in a header. This is what the module docstring above promises.
    """
    import streamlit as st

    monkeypatch.setattr(st, "secrets", {}, raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def fixture(name: str) -> Any:
    """Read a test-only payload variant."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def payload():
    """Read a test-only payload variant by name."""
    return fixture


@pytest.fixture
def settings() -> Settings:
    return Settings(api_base_url="https://api.test", api_token="test-token", env="test")


@pytest.fixture
def stall_monitor() -> Monitor:
    return SINGLE_FEED_STALL


@pytest.fixture
def http_monitor() -> Monitor:
    return HTTP_FAILURE


@pytest.fixture
def stall_page() -> IncidentPage:
    return IncidentPage.model_validate(load_sample("single_feed_stall_incidents"))


@pytest.fixture
def http_page() -> IncidentPage:
    return IncidentPage.model_validate(load_sample("http_failure_incidents"))


@pytest.fixture
def stall_trend() -> TrendResponse:
    return TrendResponse.model_validate(load_sample("single_feed_stall_trend"))


@pytest.fixture
def summary() -> SummaryResponse:
    return SummaryResponse.model_validate(load_sample("summary"))


@pytest.fixture
def contact_queue_page() -> IncidentPage:
    return IncidentPage.model_validate(load_sample("contact_queue"))


@pytest.fixture
def empty_page() -> IncidentPage:
    return IncidentPage.model_validate(fixture("incidents_empty"))
