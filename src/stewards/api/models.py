"""Pydantic models mirroring the stewards API contract.

Nothing past `client.py` sees a raw dict. `Incident.detail` is the one exception: it is
monitor-specific, so it stays untyped here and is validated against the detail model each
monitor declares in the registry (see `monitors.registry.Monitor.detail_model`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Meta(ApiModel):
    snapshot_date: date
    generated_at: datetime
    total: int = 0
    page: int = 1
    page_size: int = 0


class DetailModel(ApiModel):
    """Base for monitor-specific `detail` payloads."""


class StallDetail(DetailModel):
    last_modified: date | None = None


class HttpFailureDetail(DetailModel):
    http_status: str | None = None
    error_class: str | None = None
    error_detail: str | None = None
    last_success: date | None = None


class Incident(ApiModel):
    monitor_id: str
    publisher_id: str
    publisher_name: str
    first_detected: date
    days_open: int
    past_threshold: bool
    status: str
    feed_id: str | None = None
    feed_name: str | None = None
    feed_type: str | None = None
    feed_url: str | None = None
    consecutive_days: int | None = None
    last_contacted: date | None = None
    quality_score: int | None = None
    trend: tuple[float, ...] = ()
    detail: dict[str, Any] = Field(default_factory=dict)


class IncidentPage(ApiModel):
    data: tuple[Incident, ...]
    meta: Meta


class MonitorCount(ApiModel):
    monitor_id: str
    count: int = 0
    past_threshold_count: int = 0
    sparkline: tuple[float, ...] = ()


class Summary(ApiModel):
    publishers_monitored: int = 0
    publishers_with_issues: int = 0
    open_incidents: int = 0
    past_threshold: int = 0
    feeds: int = 0
    datasets: int = 0
    monitors: tuple[MonitorCount, ...] = ()

    #: Change against the previous snapshot. Optional: the KPI renders without a delta
    #: until the API supplies these, rather than showing a made-up zero.
    publishers_with_issues_delta: int | None = None
    open_incidents_delta: int | None = None
    past_threshold_delta: int | None = None

    def count_for(self, monitor_id: str) -> MonitorCount | None:
        return next((m for m in self.monitors if m.monitor_id == monitor_id), None)


class SummaryResponse(ApiModel):
    data: Summary
    meta: Meta


class TrendPoint(ApiModel):
    date: date
    open_count: int
    past_threshold_count: int


class TrendResponse(ApiModel):
    data: tuple[TrendPoint, ...]
    meta: Meta
