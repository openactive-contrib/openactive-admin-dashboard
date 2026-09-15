"""Pydantic models mirroring the stewards API contract.

Nothing past `client.py` sees a raw dict. `Incident.detail` is the one exception: it is
monitor-specific, so it stays untyped here and is validated against the detail model each
monitor declares in the registry (see `monitors.registry.Monitor.detail_model`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, payload: Any) -> Any:
        """Let an explicit null fall back to the field's default.

        The batch sends null for a figure it did not compute, and a detail model declares a
        collection as an empty tuple rather than as optional. Without this, one null list
        raises where an absent key would simply have defaulted — and the whole point of a
        detail model is that a field the API has not started sending yet renders as em dash
        instead of taking the page down.
        """
        if not isinstance(payload, dict):
            return payload
        return {k: v for k, v in payload.items() if v is not None}


class StallDetail(DetailModel):
    last_modified: date | None = None


class FeedIngestionErrorDetail(DetailModel):
    error_code: str | None = None
    error_message: str | None = None
    last_completed: date | None = None


class DatasetStallFeed(DetailModel):
    """One feed inside a stalled dataset, and when it last published."""

    feed_id: str | None = None
    feed_name: str | None = None
    last_published: date | None = None
    consecutive_days: int | None = None


class DatasetStallDetail(DetailModel):
    last_modified: date | None = None
    dataset_name: str | None = None
    dataset_url: str | None = None
    feed_count: int | None = None
    feeds: tuple[DatasetStallFeed, ...] = ()


class OrphanKind(DetailModel):
    """One `by_kind` breakdown row: the orphans of a single child type in one dataset."""

    kind: str | None = None
    child_count: int | None = None
    checked_count: int | None = None
    orphan_count: int | None = None
    missing_parent_count: int | None = None

    @property
    def orphan_percent(self) -> float | None:
        """Orphans as a percentage of the children actually checked, for the table's bar.

        None rather than zero when nothing was checked: a kind the crawl did not reach has
        no share to report, and a zero would read as "none orphaned".
        """
        if not self.checked_count or self.orphan_count is None:
            return None
        return 100.0 * self.orphan_count / self.checked_count


class MissingParent(DetailModel):
    """A parent id children point at that is absent from the parent feed."""

    missing_id: str | None = None
    child_count: int | None = None


class OrphanedChildrenDetail(DetailModel):
    dataset_name: str | None = None
    dataset_url: str | None = None
    child_count: int | None = None
    checked_count: int | None = None
    orphan_count: int | None = None

    #: The API reports the share as a fraction; `orphan_percent` below is the 0-100 figure
    #: the table's bar column wants, named to match `OrphanKind` so a dataset with no
    #: breakdown falls back onto the same column.
    orphan_share: float | None = None

    missing_parent_count: int | None = None
    by_kind: tuple[OrphanKind, ...] = ()
    missing_parents: tuple[MissingParent, ...] = ()

    @property
    def orphan_percent(self) -> float | None:
        return None if self.orphan_share is None else 100.0 * self.orphan_share


#: How the batch describes why a dataset or feed was flagged, in the copy the table shows.
FUTURE_DECLINE_REASONS = {
    "monotonic_decline": "Monotonic decline",
    "sharp_drop": "Sharp drop",
    "both": "Both",
}


class FutureDeclineWindow(DetailModel):
    """What a declining dataset and one of its feeds both report for the window.

    The two levels name their figures alike so a dataset the batch reported without a feed
    breakdown still fills the same columns from its own totals.
    """

    reason: str | None = None
    drop: int | None = None
    drop_percent: float | None = None

    #: Item counts the crawl saw in the window. The batch reports these per feed; a
    #: dataset-level row has none, and the delta below is then absent rather than zero.
    updated_in_window: int | None = None
    deletes_in_window: int | None = None

    @property
    def reason_label(self) -> str | None:
        """The reason token as prose. An unrecognised token still reads, de-slugged."""
        if not self.reason:
            return None
        deslugged = self.reason.replace("_", " ").capitalize()
        return FUTURE_DECLINE_REASONS.get(self.reason, deslugged)

    @property
    def delta_in_window(self) -> int | None:
        """Items updated minus items deleted over the window.

        Negative is the finding: the feed removed more than it refreshed, which is what
        pulls a dataset's future count down. None rather than zero where either figure is
        missing, because an unreported count is not a balanced one.
        """
        if self.updated_in_window is None or self.deletes_in_window is None:
            return None
        return self.updated_in_window - self.deletes_in_window

    @property
    def deficit_in_window(self) -> int | None:
        """`delta_in_window` the other way up, so descending order is worst first.

        The table sorts on this rather than on the delta itself: the row a steward wants at
        the top is the one deleting hardest, which is the *most negative* delta.
        """
        delta = self.delta_in_window
        return None if delta is None else -delta


class FutureDeclineFeed(FutureDeclineWindow):
    """One feed inside a declining dataset, and what the window did to it."""

    feed_id: str | None = None
    feed_name: str | None = None
    start_future: int | None = None
    current_future: int | None = None
    consecutive_declining_days: int | None = None
    largest_daily_drop_percent: float | None = None


class FutureDeclineDetail(FutureDeclineWindow):
    dataset_name: str | None = None
    dataset_url: str | None = None
    feed_count: int | None = None
    window_days: int | None = None
    start_total: int | None = None
    current_total: int | None = None
    feeds: tuple[FutureDeclineFeed, ...] = ()

    #: The dataset's own totals under the names its feeds use, so a dataset reported
    #: without a breakdown fills the same two columns instead of leaving them blank.
    @property
    def start_future(self) -> int | None:
        return self.start_total

    @property
    def current_future(self) -> int | None:
        return self.current_total


class Incident(ApiModel):
    monitor_id: str
    publisher_id: str
    publisher_name: str

    #: A monitor that measures a snapshot rather than an ageing fault reports no age, so
    #: both are optional. Everything that displays them renders em dash instead, and
    #: nothing recomputes `past_threshold` from them — the API owns that.
    first_detected: date | None = None
    days_open: int | None = None

    past_threshold: bool
    status: str
    feed_id: str | None = None
    feed_name: str | None = None
    feed_type: str | None = None
    feed_url: str | None = None
    consecutive_days: int | None = None
    last_contacted: date | None = None

    #: Percentage-style figures arrive fractional (`72.1`), so this is a float, not an int.
    quality_score: float | None = None

    #: The row sparkline. A snapshot the batch has no figure for arrives as null, so the
    #: series is optional per point: dropping the gaps would silently reshape the line.
    trend: tuple[float | None, ...] = ()

    detail: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _fold_extras_into_detail(cls, payload: Any) -> Any:
        """Move top-level keys this model does not declare into `detail`.

        A monitor's own measurements arrive beside the shared incident fields rather than
        nested, and `extra="ignore"` would drop them silently. Folding them in means a
        registry entry reaches them through the ordinary `detail.<name>` column path,
        validated by the monitor's own detail model, so no shared code learns their names.
        Keys already present in `detail` win: the nested value is the explicit one.
        """
        if not isinstance(payload, dict):
            return payload
        detail = payload.get("detail")
        if detail is not None and not isinstance(detail, dict):
            # Malformed rather than absent. Replacing it here would turn a broken payload
            # into a plausible one; the field rejects it and the page says the shape is off.
            return payload
        extras = {k: v for k, v in payload.items() if k not in cls.model_fields}
        if not extras and detail is not None:
            return payload
        return {**payload, "detail": {**extras, **(detail or {})}}


class IncidentPage(ApiModel):
    data: tuple[Incident, ...]
    meta: Meta


class MonitorCount(ApiModel):
    monitor_id: str

    #: Counts a deployment does not compute for this snapshot arrive as null, so the tile
    #: reads "not reported" rather than the zero that means "all clear".
    count: int | None = None
    past_threshold_count: int | None = None

    #: A snapshot the batch has no figure for arrives as null, as on `Incident.trend`.
    sparkline: tuple[float | None, ...] = ()


class Summary(ApiModel):
    #: Every fleet count is optional for the same reason as the deltas below: a figure the
    #: batch has not computed yet arrives as null, and the KPI says so instead of showing a
    #: zero that would read as "nothing is wrong".
    publishers_monitored: int | None = None
    publishers_with_issues: int | None = None
    open_incidents: int | None = None
    past_threshold: int | None = None
    feeds: int | None = None
    datasets: int | None = None
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
