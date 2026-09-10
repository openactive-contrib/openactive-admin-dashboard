"""The monitor registry — the extension point.

Adding a monitor is one entry here, one five-line page stub, one sample payload and one test
module. If a new monitor forces a change to a component, generalise the component instead.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from stewards.api.models import (
    DetailModel,
    FeedIngestionErrorDetail,
    OrphanedChildrenDetail,
    OrphanKind,
    StallDetail,
)
from stewards.monitors.health import Direction, HealthPolicy
from stewards.monitors.tile_viz import Gauge, Sparkline, TileViz


class Group(StrEnum):
    OVERVIEW = "Overview"
    AVAILABILITY = "Availability"
    CONTENT = "Content"
    COVERAGE = "Coverage & quality"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFORMATIONAL = "informational"


class ColKind(StrEnum):
    TEXT = "text"
    MONO = "mono"
    NUMBER = "number"
    DATE = "date"
    DAYS = "days"
    PERCENT = "percent"
    SCORE = "score"

    #: A 0-100 figure where high is bad, the mirror of PERCENT. A share of something
    #: broken needs the opposite shading, or a wholly orphaned dataset renders green.
    RISK = "risk"
    SPARKLINE = "sparkline"
    STATUS = "status"
    LINK = "link"


#: Kinds that carry RAG semantics and are therefore background-shaded in the table.
RAG_KINDS = frozenset(
    {ColKind.DAYS, ColKind.STATUS, ColKind.PERCENT, ColKind.SCORE, ColKind.RISK}
)


@dataclass(frozen=True, slots=True)
class Col:
    """One table column. `field` is a path on `Incident`, or `detail.<name>`."""

    field: str
    label: str
    kind: ColKind = ColKind.TEXT
    primary: bool = False
    help: str | None = None

    @property
    def is_detail(self) -> bool:
        return self.field.startswith("detail.")

    @property
    def detail_attr(self) -> str:
        return self.field.removeprefix("detail.")

    @property
    def is_part(self) -> bool:
        """True for a column reading the exploded row rather than the incident."""
        return self.field.startswith(PART_PREFIX)


#: Field-path prefix for the item an exploded row was built from. See `Monitor.rows`.
PART_PREFIX = "part."


@dataclass(frozen=True, slots=True)
class RowSpec:
    """Explode each incident into one table row per item of a detail list field.

    A monitor whose incident is a measurement rather than a single fault often carries its
    answer as a breakdown — orphans per child type, say. Declaring the breakdown here makes
    each item a row, addressable as `part.<name>`, instead of collapsing it into one cell.
    """

    field: str
    item_model: type[DetailModel]


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """A selectbox whose options are the distinct values present in the snapshot."""

    field: str
    label: str


@dataclass(frozen=True, slots=True)
class Monitor:
    id: str
    name: str
    group: Group
    severity: Severity
    blurb: str
    unit: str
    columns: tuple[Col, ...]
    key_cols: tuple[str, ...] = ("publisher_id", "feed_id")
    detail_model: type[DetailModel] = DetailModel
    summary_field: str = "feed_name"
    threshold_days: int = 7
    health: HealthPolicy = field(default_factory=HealthPolicy)

    #: How the overview card draws this monitor's figure. See `monitors.tile_viz`.
    viz: TileViz = field(default_factory=Sparkline)

    #: Set to explode each incident into several table rows. None means one row per incident.
    rows: RowSpec | None = None

    #: Field the table is ordered by, descending, then publisher name. The default suits a
    #: monitor whose incidents age; one that measures a volume orders by the volume.
    sort_field: str = "days_open"

    #: When set, the first KPI is the sum of this field over the shown rows rather than a
    #: count of them — the headline figure for a monitor that measures a quantity.
    kpi_sum_field: str | None = None

    has_threshold_filter: bool = True

    #: Help text on the past-threshold toggle. The default names the day count, which only
    #: makes sense for a monitor whose incidents have an age.
    threshold_help: str = ""

    filters: tuple[FilterSpec, ...] = ()
    extras: tuple[str, ...] = ()
    schedule: str = "daily 04:00 UTC"
    query: str = ""
    page: str = ""
    kpi_labels: tuple[str, str, str] = field(
        default=("", "publishers affected", "past threshold")
    )

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError(f"monitor {self.id} declares no columns")

    @property
    def crumb(self) -> str:
        return f"{self.group.value} monitor"

    @property
    def meta_chips(self) -> tuple[str, ...]:
        return (
            f"monitor.{self.id}",
            f"severity: {self.severity.value}",
            f"contact after {self.threshold_days}d",
            self.schedule,
        )

    def column(self, label: str) -> Col:
        """Look a column up by its rendered label."""
        for col in self.columns:
            if col.label == label:
                return col
        raise KeyError(label)


SINGLE_FEED_STALL = Monitor(
    id="single_feed_stall",
    name="Single-feed stalls",
    group=Group.AVAILABILITY,
    severity=Severity.HIGH,
    blurb=(
        "Individual feeds that have not published new data for at least 5 days, despite "
        "having published data within the last 120 days. Feeds that are part of a wider "
        "dataset issue are reported separately as dataset-wide stalls."
    ),
    unit="feeds stalled",
    detail_model=StallDetail,
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("feed_type", "Type", ColKind.TEXT),
        Col("detail.last_modified", "Last modified", ColKind.DATE),
        Col("days_open", "Days stalled", ColKind.DAYS),
        Col(
            "trend",
            "Recent trend",
            ColKind.SPARKLINE,
            help="The most recent daily snapshots; one with no figure is omitted",
        ),
        Col("status", "Status", ColKind.STATUS),
        Col("feed_url", "Endpoint", ColKind.LINK, help="Opens the publisher's feed endpoint"),
        Col("feed_id", "Feed", ColKind.MONO),
    ),
    filters=(
        FilterSpec("feed_type", "Feed type"),
        FilterSpec("status", "Status"),
    ),
    query="monitor_single_feed_stall_v2",
    page="views/10_single_feed_stalls.py",
    kpi_labels=("feeds stalled", "publishers affected", "past threshold"),
)

FEED_INGESTION_ERROR = Monitor(
    id="feed_ingestion_error",
    name="Feed ingestion errors",
    group=Group.AVAILABILITY,
    severity=Severity.HIGH,
    blurb=(
        "Feeds that the daily crawl could not ingest because the endpoint returned an "
        "error, such as a non-200 status, TLS error, or timeout. Only includes feeds that "
        "have successfully run at least once in the past 15 days but failed during the "
        "latest crawl."
    ),
    unit="feeds failing ingestion",
    detail_model=FeedIngestionErrorDetail,
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("detail.error_code", "Error code", ColKind.MONO),
        Col("days_open", "Consecutive failures", ColKind.DAYS),
        Col("detail.last_completed", "Last completed", ColKind.DATE),
        Col(
            "trend",
            "Recent trend",
            ColKind.SPARKLINE,
            help="The most recent daily snapshots; one with no figure is omitted",
        ),
        Col("feed_url", "Endpoint", ColKind.LINK, help="Opens the publisher's feed endpoint"),
        Col("feed_id", "Feed", ColKind.MONO),
        Col(
            "detail.error_message",
            "Error message",
            ColKind.TEXT,
            help="The error the crawl recorded; hover a cell to read it in full",
        ),
    ),
    filters=(
        FilterSpec("detail.error_code", "Error code"),
        FilterSpec("feed_type", "Feed type"),
    ),
    schedule="daily 04:00 UTC · suppress 1 day",
    query="monitor_feed_ingestion_error_v1",
    page="views/12_feed_ingestion_errors.py",
    kpi_labels=("feeds failing ingestion", "publishers affected", "past threshold"),
)


DATASET_ORPHANED_CHILDREN = Monitor(
    id="dataset_orphaned_children",
    name="Orphaned children",
    group=Group.CONTENT,
    severity=Severity.HIGH,
    blurb=(
        "Child items whose parent is absent from the feed that should contain it: "
        "ScheduledSessions whose superEvent is missing from the SessionSeries feed, and "
        "Slots without their FacilityUse. A consumer cannot render these items at all, "
        "because the parent carries the name, location and activity. Counted per dataset "
        "over the children the crawl reached, so a dataset whose parent feed failed to "
        "ingest is reported by the ingestion monitor rather than counted as orphans here."
    ),
    unit="orphaned children",
    detail_model=OrphanedChildrenDetail,
    # The answer a steward needs is which child type is orphaned, so each dataset becomes
    # one row per kind rather than one row carrying a collapsed breakdown.
    rows=RowSpec("detail.by_kind", OrphanKind),
    # The batch reports no history for this monitor yet — `sparkline` is empty and the trend
    # endpoint is not deployed — so the card reads today's figure against a fixed benchmark
    # instead of against a series it does not have.
    viz=Gauge(benchmark=785_000),
    # A volume, not a fault count: only its movement can be judged, so there is no level at
    # which the monitor is clear.
    health=HealthPolicy(direction=Direction.UP_IS_BAD, clear_level=None),
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("detail.dataset_name", "Dataset", ColKind.TEXT),
        Col("part.kind", "Child type", ColKind.TEXT),
        Col("part.orphan_count", "Orphans", ColKind.NUMBER),
        Col(
            "part.checked_count",
            "Children checked",
            ColKind.NUMBER,
            help="Children of this type the crawl resolved a parent for, or failed to",
        ),
        Col("part.orphan_percent", "Share orphaned", ColKind.RISK),
        Col(
            "part.missing_parent_count",
            "Missing parents",
            ColKind.NUMBER,
            help="Distinct parent ids these children point at that the feed does not contain",
        ),
        Col("detail.dataset_url", "Dataset feed", ColKind.LINK),
    ),
    filters=(FilterSpec("part.kind", "Child type"),),
    sort_field="part.orphan_count",
    kpi_sum_field="part.orphan_count",
    threshold_help="Show only the datasets the API has flagged past its own threshold.",
    summary_field="detail.dataset_name",
    schedule="daily 04:00 UTC",
    query="monitor_dataset_orphaned_children_v1",
    page="views/22_dataset_orphaned_children.py",
    kpi_labels=("orphaned children", "publishers affected", "datasets flagged"),
)


#: Ordered registry. The overview and the sidebar iterate this — never a hard-coded list.
MONITOR_REGISTRY: tuple[Monitor, ...] = (
    SINGLE_FEED_STALL,
    FEED_INGESTION_ERROR,
    DATASET_ORPHANED_CHILDREN,
)

_BY_ID: Mapping[str, Monitor] = {m.id: m for m in MONITOR_REGISTRY}


def get_monitor(monitor_id: str) -> Monitor:
    try:
        return _BY_ID[monitor_id]
    except KeyError as exc:
        raise KeyError(f"unknown monitor {monitor_id!r}") from exc


def monitor_ids() -> tuple[str, ...]:
    return tuple(_BY_ID)


def monitors_in_group(group: Group) -> Iterator[Monitor]:
    return (m for m in MONITOR_REGISTRY if m.group is group)


def groups() -> tuple[Group, ...]:
    """Groups that actually have monitors, in registry order."""
    seen: list[Group] = []
    for monitor in MONITOR_REGISTRY:
        if monitor.group not in seen:
            seen.append(monitor.group)
    return tuple(seen)
