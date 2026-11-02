"""Incident and notification-row builders for the outreach tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from stewards.api.models import Incident, PublisherNotification

DATASET = "https://alpha.example/opendata"


def feed_incident(
    monitor_id: str,
    feed_id: str,
    days_open: int | None,
    *,
    feed_type: str | None = "ScheduledSession",
    dataset_url: str | None = DATASET,
    publisher_id: str = "pub_alpha",
    publisher_name: str = "Alpha Leisure",
    **extra: Any,
) -> Incident:
    return Incident.model_validate(
        {
            "monitor_id": monitor_id,
            "publisher_id": publisher_id,
            "publisher_name": publisher_name,
            "first_detected": "2026-09-20",
            "days_open": days_open,
            "past_threshold": False,
            "status": "open",
            "feed_id": feed_id,
            "feed_name": f"{feed_type} feed",
            "feed_type": feed_type,
            "feed_url": f"{dataset_url or 'https://x.example'}/{feed_id}",
            "dataset_url": dataset_url,
            "dataset_name": "Alpha Sessions and Facilities",
            "trend": [1, 2],
            **extra,
        }
    )


def dataset_incident(
    monitor_id: str,
    days_open: int | None = None,
    *,
    dataset_url: str | None = DATASET,
    publisher_id: str = "pub_alpha",
    publisher_name: str = "Alpha Leisure",
    **detail: Any,
) -> Incident:
    return Incident.model_validate(
        {
            "monitor_id": monitor_id,
            "publisher_id": publisher_id,
            "publisher_name": publisher_name,
            "first_detected": "2026-09-20" if days_open is not None else None,
            "days_open": days_open,
            "past_threshold": False,
            "status": "open",
            "detail": {"dataset_url": dataset_url, "dataset_name": "Alpha data", **detail},
        }
    )


def scheduled_feeds(*feed_types: str) -> list[dict[str, Any]]:
    return [
        {"feed_id": f"f{n}", "feed_name": f"{t} feed", "feed_type": t}
        for n, t in enumerate(feed_types)
    ]


def row(
    row_id: str = "pn_alpha",
    *,
    problems: Any = (),
    status: str = "open",
    publisher_id: str = "pub_alpha",
    publisher_name: str = "Alpha Leisure",
    created_at: datetime | None = None,
    **fields: Any,
) -> PublisherNotification:
    return PublisherNotification.model_validate(
        {
            "id": row_id,
            "publisher_id": publisher_id,
            "publisher_name": publisher_name,
            "status": status,
            "contact": "someone@alpha.example",
            "problems": list(problems) if isinstance(problems, tuple) else problems,
            "created_at": created_at.isoformat() if created_at else None,
            **fields,
        }
    )
