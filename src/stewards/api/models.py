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


class NullTolerantModel(ApiModel):
    """An API model where an explicit null falls back to the field's own default.

    The batch sends null for a figure it did not compute, and these models declare a
    collection as an empty tuple rather than as optional. Without this, one null list
    raises where an absent key would simply have defaulted — and the whole point is that a
    field the API has not started sending yet renders as em dash instead of taking the page
    down.
    """

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        return {k: v for k, v in payload.items() if v is not None}


class DetailModel(NullTolerantModel):
    """Base for monitor-specific `detail` payloads."""


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
        if not self.child_count or self.orphan_count is None:
            return None
        return 100.0 * self.orphan_count / self.child_count


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


# --- feed quality -------------------------------------------------------------------------
#
# The quality endpoint is not an incident feed: it reports one row per feed with this
# snapshot's measurements, plus a fleet-wide `summary` block beside them, and it has no
# history at all. It therefore gets its own response models rather than being folded into
# `Incident`, which would mean inventing a `first_detected` and a `past_threshold` the batch
# never reported.


class CompletenessStat(NullTolerantModel):
    """Fleet-wide average for one recommended field, and how many feeds reported it."""

    average: float | None = None
    feeds_reporting: int | None = None


#: A true en dash, for the score band labels. Written as an escape so the source stays
#: unambiguously ASCII, as `monitors.overview.MINUS` does for its sign.
EN_DASH = "\u2013"


class ScoreBucket(NullTolerantModel):
    """One column of the score histogram: the band `[lower, upper)` and the feeds in it."""

    lower: float = 0.0
    upper: float = 0.0
    feed_count: int = 0

    @property
    def label(self) -> str:
        return f"{self.lower:.0f}{EN_DASH}{self.upper:.0f}"


class QualityBreakdown(NullTolerantModel):
    """How many feeds and datasets carry one value of a categorical field."""

    value: str = ""
    feed_count: int = 0
    dataset_count: int = 0
    share: float | None = None


class FeedQualitySummary(NullTolerantModel):
    """The fleet-wide block the quality endpoint sends beside its rows.

    Every figure is optional for the same reason as `/summary`: a batch that does not
    compute one sends null, and null is not zero.
    """

    total_feeds: int | None = None
    total_datasets: int | None = None
    total_publishers: int | None = None
    regular_feeds: int | None = None
    irregular_feeds: int | None = None
    regularity_unknown: int | None = None
    feeds_ok: int | None = None
    feeds_with_warnings: int | None = None
    feeds_with_errors: int | None = None
    feeds_status_unknown: int | None = None
    datasets_with_errors: int | None = None
    feeds_with_future_data: int | None = None
    datasets_with_future_data: int | None = None
    total_future_opportunity_items: int | None = None
    feeds_scored: int | None = None
    average_score: float | None = None
    median_score: float | None = None
    min_score: float | None = None
    max_score: float | None = None
    score_buckets: tuple[ScoreBucket, ...] = ()

    #: Recommended field -> its fleet-wide average. The set of fields is the batch's to
    #: decide, so it stays a mapping rather than becoming nine declared attributes.
    completeness: dict[str, CompletenessStat] = Field(default_factory=dict)

    status_breakdown: tuple[QualityBreakdown, ...] = ()
    grade_breakdown: tuple[QualityBreakdown, ...] = ()
    feed_type_breakdown: tuple[QualityBreakdown, ...] = ()
    feed_version_breakdown: tuple[QualityBreakdown, ...] = ()
    oldest_assessment: datetime | None = None
    newest_assessment: datetime | None = None


class FeedQualityFeed(NullTolerantModel):
    """One feed's quality assessment in this snapshot."""

    feed_id: str = ""
    feed_url: str = ""
    feed_type: str = ""
    feed_version: str = ""
    is_regular: bool | None = None
    dataset_url: str = ""
    dataset_name: str = ""
    publisher_id: str = ""
    publisher_name: str = ""

    #: `OK`, `WARNING` or `ERROR` from this API; `monitors.thresholds` tones it.
    status: str = ""

    grade: str | None = None

    #: The 0-100 quality score, absent for a feed the batch could not score.
    score: float | None = None

    num_future_opportunity_items: int | None = None

    #: Recommended field -> the share of this feed's items carrying it, 0-100. A field the
    #: assessment did not reach is null, which is not the same as zero coverage.
    completeness: dict[str, float | None] = Field(default_factory=dict)

    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    #: Opportunity type -> the required fields missing from it.
    missing_required_fields: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    last_assessed: datetime | None = None


class FeedQualityResponse(ApiModel):
    data: tuple[FeedQualityFeed, ...] = ()
    summary: FeedQualitySummary = Field(default_factory=FeedQualitySummary)
    meta: Meta


# --- active places coverage ---------------------------------------------------------------
#
# The third backing read. Like the quality snapshot it reports this run's measurements with
# no history, so it is not an incident feed; unlike the quality snapshot its figures and its
# rows are two endpoints, and the rows are paginated. Only the fields the page renders are
# declared — `extra="ignore"` carries the rest of the payload, and a field earns a line here
# when something draws it.


class CoverageHeadline(NullTolerantModel):
    """The run's top-line figures, both ways round: sites covered, and venues not."""

    coverage_pct: float | None = None
    sites_total: int | None = None
    sites_matched: int | None = None
    sites_missing: int | None = None
    local_authorities: int | None = None
    venues_total: int | None = None
    venues_matched: int | None = None
    venues_unmatched: int | None = None
    venues_unmatched_pct: float | None = None


class CoverageGroup(NullTolerantModel):
    """One row of a coverage breakdown — by region here.

    The same shape backs the ownership, management and facility-type breakdowns, so a second
    chart over any of them needs a field on `CoverageSnapshot` and nothing else.
    """

    region_name: str = ""
    sites_total: int | None = None
    sites_matched: int | None = None
    sites_missing: int | None = None
    coverage_pct: float | None = None


class CoverageParameters(NullTolerantModel):
    """The thresholds the run was computed at, for the page's own copy."""

    buffer_metres: float | None = None
    postcode_max_metres: float | None = None
    name_max_metres: float | None = None


class CoverageSource(NullTolerantModel):
    active_places_data_version: str = ""
    geography_scope: str = ""
    excluded_kinds: tuple[str, ...] = ()


class CoverageSnapshot(NullTolerantModel):
    run_date: date | None = None
    headline: CoverageHeadline = Field(default_factory=CoverageHeadline)
    parameters: CoverageParameters = Field(default_factory=CoverageParameters)
    source: CoverageSource = Field(default_factory=CoverageSource)
    coverage_by_region: tuple[CoverageGroup, ...] = ()


class CoverageResponse(ApiModel):
    data: CoverageSnapshot = Field(default_factory=CoverageSnapshot)
    meta: Meta


class SiteMapping(NullTolerantModel):
    """One site-venue pair: an Active Places site and an OpenActive venue matched to it.

    The coordinates and the raw `oa_location_json` are deliberately not declared: the app
    draws no map, and the distance the batch already computed is the figure a reader needs.
    """

    site_id: str = ""
    site_name: str = ""
    postcode: str = ""
    local_authority_name: str = ""
    ownership_type_group: str = ""
    ap_facility_count: int | None = None
    oa_location_names: tuple[str, ...] = ()
    oa_dataset_urls: tuple[str, ...] = ()
    oa_publisher_names: tuple[str, ...] = ()
    oa_postal_codes: tuple[str, ...] = ()
    oa_kinds: tuple[str, ...] = ()
    oa_opportunity_count: int | None = None
    distance_metres: float | None = None

    #: `spatial`, `spatial_and_postcode`, `spatial_centroid_only`, `postcode` or `name`.
    match_method: str = ""

    #: 0-1, and only the name channel reports one.
    name_similarity: float | None = None

    is_primary_for_venue: bool = False
    is_mutual_best: bool = False


class SiteMappingPage(ApiModel):
    data: tuple[SiteMapping, ...] = ()
    meta: Meta
