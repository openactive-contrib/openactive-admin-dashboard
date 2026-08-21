"""The monitor registry — the extension point.

Adding a monitor is one entry here, one five-line page stub, one sample payload and one test
module. If a new monitor forces a change to a component, generalise the component instead.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from stewards.api.models import DetailModel, HttpFailureDetail, StallDetail


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
    SPARKLINE = "sparkline"
    STATUS = "status"
    LINK = "link"


#: Kinds that carry RAG semantics and are therefore background-shaded in the table.
RAG_KINDS = frozenset({ColKind.DAYS, ColKind.STATUS, ColKind.PERCENT, ColKind.SCORE})


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
    has_threshold_filter: bool = True
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
        "Individual feeds whose max(modified) has not advanced across consecutive daily "
        "snapshots while the endpoint still returns 200. Sibling feeds on the same dataset "
        "are excluded when the whole dataset is stalled — those appear under dataset-wide "
        "stalls instead."
    ),
    unit="feeds stalled",
    detail_model=StallDetail,
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("feed_name", "Feed", ColKind.MONO),
        Col("feed_type", "Type", ColKind.TEXT),
        Col("detail.last_modified", "Last modified", ColKind.DATE),
        Col("days_open", "Days stalled", ColKind.DAYS),
        Col("trend", "30d trend", ColKind.SPARKLINE),
        Col("status", "Status", ColKind.STATUS),
        Col("feed_url", "Endpoint", ColKind.LINK, help="Opens the publisher's feed endpoint"),
    ),
    filters=(
        FilterSpec("feed_type", "Feed type"),
        FilterSpec("status", "Status"),
    ),
    query="monitor_single_feed_stall_v2",
    page="views/10_single_feed_stalls.py",
    kpi_labels=("feeds stalled", "publishers affected", "past threshold"),
)

HTTP_FAILURE = Monitor(
    id="http_failure",
    name="HTTP endpoint failures",
    group=Group.AVAILABILITY,
    severity=Severity.HIGH,
    blurb=(
        "Feed endpoints returning a non-200 status, TLS error or timeout on consecutive "
        "daily fetches. Single-day blips are suppressed; an incident opens on the second "
        "consecutive failure and carries the last successful fetch."
    ),
    unit="endpoints failing",
    detail_model=HttpFailureDetail,
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("feed_name", "Feed", ColKind.MONO),
        Col("detail.http_status", "HTTP", ColKind.MONO),
        Col("detail.error_class", "Error", ColKind.TEXT),
        Col("days_open", "Consecutive failures", ColKind.DAYS),
        Col("detail.last_success", "Last success", ColKind.DATE),
        Col("status", "Status", ColKind.STATUS),
        Col("feed_url", "Endpoint", ColKind.LINK, help="Opens the publisher's feed endpoint"),
    ),
    filters=(
        FilterSpec("detail.http_status", "Status code"),
        FilterSpec("detail.error_class", "Error class"),
    ),
    schedule="daily 04:00 UTC · suppress 1 day",
    query="monitor_http_failure_v3",
    page="views/12_http_failures.py",
    kpi_labels=("endpoints failing", "publishers affected", "past threshold"),
)

#: Ordered registry. The overview and the sidebar iterate this — never a hard-coded list.
MONITOR_REGISTRY: tuple[Monitor, ...] = (
    SINGLE_FEED_STALL,
    HTTP_FAILURE,
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
