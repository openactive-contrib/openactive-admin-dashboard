"""Smoke-test wiring: every page renders against the bundled payloads, never the network."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from stewards import config
from stewards.api import client as client_module


@pytest.fixture(autouse=True)
def sample_data_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the pages in sample-data mode with the auth gate off."""
    for key in ("STEWARDS_API_BASE_URL", "STEWARDS_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("STEWARDS_USE_SAMPLE_DATA", "true")
    monkeypatch.setenv("STEWARDS_ENV", "dev")
    monkeypatch.setenv("STEWARDS_DISABLE_AUTH", "true")
    config.get_settings.cache_clear()
    client_module.reset_client()
    yield
    client_module.reset_client()
    config.get_settings.cache_clear()
