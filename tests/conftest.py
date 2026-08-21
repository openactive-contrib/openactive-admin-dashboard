"""Shared fixtures. No test touches the network, a real token, or BigQuery.

Happy-path payloads are the ones bundled in `stewards/api/sample_data` — the app serves the
same files in sample-data mode, so there is one copy of each contract shape. The variants a
test needs and the app does not (empty, malformed, paginated) live in `tests/fixtures`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from stewards.api.models import IncidentPage, SummaryResponse, TrendResponse
from stewards.api.sample_transport import load_sample
from stewards.config import Settings
from stewards.monitors.registry import HTTP_FAILURE, SINGLE_FEED_STALL, Monitor

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SNAPSHOT_DATE = date(2026, 8, 21)


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
