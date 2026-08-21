"""Serves the bundled sample payloads so the app runs before the API exists.

Enabled with `STEWARDS_USE_SAMPLE_DATA=true`. The payloads in `sample_data/` double as the
happy-path contract fixtures for the tests, so there is one copy of each shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

SAMPLE_DIR = Path(__file__).parent / "sample_data"


def load_sample(name: str) -> Any:
    """Read a bundled payload by file stem."""
    return json.loads((SAMPLE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _resolve(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    match parts:
        case [*_, "summary"]:
            return "summary"
        case [*_, "contact-queue"]:
            return "contact_queue"
        case [*_, "monitors", monitor_id, "incidents"]:
            return f"{monitor_id}_incidents"
        case [*_, "monitors", monitor_id, "trend"]:
            return f"{monitor_id}_trend"
        case _:
            return None


def _handler(request: httpx.Request) -> httpx.Response:
    name = _resolve(request.url.path)
    if name is None or not (SAMPLE_DIR / f"{name}.json").exists():
        return httpx.Response(404, json={"error": f"no sample payload for {request.url.path}"})
    return httpx.Response(200, json=load_sample(name))


def sample_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_handler)
