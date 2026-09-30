"""The monitor registry — the extension point.

Adding a monitor is one entry here, one five-line page stub, one sample payload and one test
module. If a new monitor forces a change to a component, generalise the component instead.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from stewards.api.models import (
    DatasetStallDetail,
    DetailModel,
    FeedIngestionErrorDetail,
    FutureDeclineDetail,
    FutureDeclineFeed,
    OrphanedChildrenDetail,
    OrphanKind,
    StallDetail,
)
from stewards.monitors.health import Direction, HealthPolicy
from stewards.monitors.tile_viz import Facts, Gauge, Sparkline, TileViz


class Group(StrEnum):
    OVERVIEW = "Overview"
    AVAILABILITY = "Availability"
    CONTENT = "Content"
    COVERAGE = "Coverage & quality"


class Source(StrEnum):
    """Which logical read backs a monitor, and therefore which page renders it.

    `INCIDENTS` is a list of faults that age — the shape every shared component assumes.
    `QUALITY` is a snapshot of the whole fleet with a summary block beside it and no
    history, which cannot be folded into an incident without inventing the age and the
    threshold flag the batch never reported. See `monitors.quality`.
    `COVERAGE` is a snapshot of how much of an external estate the fleet reaches: figures
    from one read and rows from a second, paginated one, and no history either. It is
    neither of the others — its rows do not age, and its figures do not arrive beside them.
    See `monitors.coverage`.
    `SCHEMA_DRIFT` is a snapshot of the custom properties the fleet publishes: rows and a
    summary block in one paginated read, and no history. It arrives shaped like a quality
    snapshot but measures nothing a score describes — each row is a feed and the properties
    it uses that the OpenActive vocabulary does not define. See `monitors.schema_drift`.
    """

    INCIDENTS = "incidents"
    QUALITY = "quality"
    COVERAGE = "coverage"
    SCHEMA_DRIFT = "schema_drift"


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

    #: What a `LINK` cell reads, where "feed" is the wrong noun for what it opens.
    link_text: str | None = None

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

    #: A `part.<name>` path whose rows are dropped where it is exactly zero: a breakdown item
    #: with nothing in it is not a finding. A null is kept, because a figure the batch did not
    #: report is not a zero. None keeps every row.
    hide_zero: str | None = None


@dataclass(frozen=True, slots=True)
class RowDetail:
    """A table shown under the incident table when a row is selected.

    `field` names a detail field holding a list of models — the parents a dataset's children
    are missing, the feeds a stalled dataset covers — and `columns` are read against each
    item with the `part.` prefix, exactly as the main table reads an exploded row. Declaring
    it here is what keeps the renderer generic: no component knows which monitor it is
    drawing.
    """

    field: str
    title: str
    caption: str
    columns: tuple[Col, ...]

    #: Optional detail field holding the true total, for a caption saying `{count}` where the
    #: API reports only the worst few items.
    count_field: str | None = None


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

    #: Which read backs this monitor. `QUALITY` swaps the incident machinery — ageing,
    #: contact threshold, trend, email draft — for the fleet snapshot `monitors.quality`
    #: shapes, and routes the page to `components.quality_page`; `COVERAGE` does the same
    #: through `monitors.coverage` and `components.coverage_page`, and `SCHEMA_DRIFT`
    #: through `monitors.schema_drift` and `components.schema_drift_page`.
    source: Source = Source.INCIDENTS

    #: The row resource's own id, for a monitor whose rows are a second endpoint rather than
    #: arriving beside its figures. Only a `COVERAGE` monitor declares one.
    rows_id: str = ""

    key_cols: tuple[str, ...] = ("publisher_id", "feed_id")
    detail_model: type[DetailModel] = DetailModel
    summary_field: str = "feed_name"
    threshold_days: int = 7
    health: HealthPolicy = field(default_factory=HealthPolicy)

    #: How the overview card draws this monitor's figure. See `monitors.tile_viz`.
    viz: TileViz = field(default_factory=Sparkline)

    #: Set to explode each incident into several table rows. None means one row per incident.
    rows: RowSpec | None = None

    #: A table shown beneath the incident table for the selected row, where the monitor's
    #: detail carries a list worth its own columns.
    row_detail: RowDetail | None = None

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

    #: What one incident of this monitor is, in publisher-facing copy: "this <entity> has
    #: stopped publishing". A monitor whose incident is a whole dataset says so.
    entity: str = "feed"

    #: Label and field path per line of the identifying block in the publisher email. The
    #: default names a feed; a dataset-level monitor names its dataset.
    email_fields: tuple[tuple[str, str], ...] = (
        ("Feed", "feed_name"),
        ("Feed type", "feed_type"),
        ("Endpoint", "feed_url"),
    )

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
        """The chips under the blurb. A monitor whose rows do not age states no threshold."""
        threshold = (
            (f"contact after {self.threshold_days}d",)
            if self.source is Source.INCIDENTS
            else ()
        )
        return (
            f"monitor.{self.id}",
            f"severity: {self.severity.value}",
            *threshold,
            self.schedule,
        )

    def column(self, label: str) -> Col:
        """Look a column up by its rendered label."""
        for col in self.columns:
            if col.label == label:
                return col
        raise KeyError(label)


DATASET_STALL = Monitor(
    id="dataset_stall",
    name="Dataset-wide stalls",
    group=Group.AVAILABILITY,
    # Strictly worse than a single stalled feed: nothing at all is reaching consumers from
    # this publisher, so it is the first card a steward should see.
    severity=Severity.CRITICAL,
    blurb=(
        "Datasets in which every feed has stopped publishing new or updated items for at "
        "least 5 days, despite having published within the last 120 days. Nothing is "
        "reaching consumers from the publisher at all, so the whole dataset is frozen "
        "rather than one part of it. A dataset silent for longer than the 120-day lookback "
        "counts as retired, not stalled, and the feeds inside a frozen dataset are reported "
        "here once instead of as unrelated single-feed stalls."
    ),
    unit="datasets frozen",
    detail_model=DatasetStallDetail,
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("detail.dataset_name", "Dataset", ColKind.TEXT),
        Col(
            "detail.feed_count",
            "Feeds",
            ColKind.NUMBER,
            help="How many feeds this dataset publishes, all of them silent",
        ),
        Col("detail.last_modified", "Last published", ColKind.DATE),
        Col("days_open", "Days stalled", ColKind.DAYS),
        Col(
            "trend",
            "Recent trend",
            ColKind.SPARKLINE,
            help="The most recent daily snapshots; one with no figure is omitted",
        ),
        Col(
            "detail.dataset_url", "Dataset feed", ColKind.LINK, help="Opens the dataset's feed"
        ),
    ),
    # The whole dataset is one incident, so its feeds are the breakdown a steward opens
    # rather than rows of their own: the point of this monitor is that they failed together.
    row_detail=RowDetail(
        field="detail.feeds",
        title="Frozen feeds",
        caption=(
            "Every feed in this dataset, and when each last published. A feed with no date "
            "has not published inside the lookback window at all."
        ),
        columns=(
            Col("part.feed_name", "Feed", ColKind.TEXT, primary=True),
            Col("part.last_published", "Last published", ColKind.DATE),
            Col("part.consecutive_days", "Days silent", ColKind.DAYS),
            Col("part.feed_id", "Feed id", ColKind.MONO),
        ),
    ),
    # The admin API reports every incident as `open`, so there is no status worth a column,
    # and nothing categorical to filter a frozen dataset by — search and the threshold
    # toggle are the controls that mean something here.
    filters=(),
    summary_field="detail.dataset_name",
    entity="dataset",
    email_fields=(
        ("Dataset", "detail.dataset_name"),
        ("Feeds", "detail.feed_count"),
        ("Endpoint", "detail.dataset_url"),
    ),
    query="monitor_dataset_stall_v1",
    page="views/10_dataset_stalls.py",
    kpi_labels=("datasets frozen", "publishers affected", "past threshold"),
)


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
    page="views/11_single_feed_stalls.py",
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
    # A child type with no orphans in a dataset is not a finding, so its row is not shown.
    rows=RowSpec("detail.by_kind", OrphanKind, hide_zero="part.orphan_count"),
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
            "part.child_count",
            "Child Count",
            ColKind.NUMBER,
            help="Number of children of this type the crawl reached in the dataset",
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
    row_detail=RowDetail(
        field="detail.missing_parents",
        title="Missing parents",
        caption=(
            "The parent ids these children reference that the dataset's parent feed does "
            "not contain, worst first. The API reports the largest offenders, not all "
            "{count} it counted."
        ),
        count_field="detail.missing_parent_count",
        columns=(
            Col("part.missing_id", "Missing parent id", ColKind.TEXT, primary=True),
            Col("part.child_count", "Children affected", ColKind.NUMBER),
        ),
    ),
    filters=(FilterSpec("part.kind", "Child type"),),
    sort_field="part.orphan_count",
    kpi_sum_field="part.orphan_count",
    threshold_help="Show only the datasets the API has flagged past its own threshold.",
    summary_field="detail.dataset_name",
    entity="dataset",
    email_fields=(
        ("Dataset", "detail.dataset_name"),
        ("Endpoint", "detail.dataset_url"),
    ),
    schedule="daily 04:00 UTC",
    query="monitor_dataset_orphaned_children_v1",
    page="views/22_dataset_orphaned_children.py",
    kpi_labels=("orphaned children", "publishers affected", "datasets flagged"),
)


DATASET_FUTURE_DECLINE = Monitor(
    id="dataset_future_decline",
    name="Future opportunity decline",
    group=Group.CONTENT,
    severity=Severity.HIGH,
    blurb=(
        "Datasets whose count of opportunities starting in the future has fallen every day "
        "of the last 5 snapshots, or has dropped sharply within that window. The feed rows "
        "carry what the window did to each feed: how many items it updated, how many it "
        "deleted, and the difference between the two, which is what a falling future count "
        "usually comes down to. A dataset that has stopped publishing altogether is "
        "reported as a stall rather than a decline."
    ),
    unit="datasets declining",
    detail_model=FutureDeclineDetail,
    # The question is which feed is pulling the dataset down, so each dataset becomes one
    # row per feed rather than one row hiding its feeds in a cell.
    rows=RowSpec("detail.feeds", FutureDeclineFeed),
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("detail.dataset_name", "Dataset", ColKind.TEXT),
        Col("part.feed_name", "Feed", ColKind.MONO),
        Col("part.reason_label", "Reason", ColKind.TEXT),
        Col(
            "part.current_future",
            "Future now",
            ColKind.NUMBER,
            help="Opportunities starting after the snapshot date, at the end of the window",
        ),
        Col(
            "part.drop_percent",
            "Drop",
            ColKind.RISK,
            help="Share of the window's starting future count that has gone",
        ),
        Col(
            "part.updated_in_window",
            "Updated",
            ColKind.NUMBER,
            help="Items the crawl saw created or updated during the window",
        ),
        Col(
            "part.deletes_in_window",
            "Deletes",
            ColKind.NUMBER,
            help="Items the feed deleted during the window",
        ),
        Col(
            "part.delta_in_window",
            "Delta",
            ColKind.NUMBER,
            help="Updated minus deletes. Negative means it removed more than it refreshed",
        ),
        Col("days_open", "Days declining", ColKind.DAYS),
        Col(
            "trend",
            "Recent trend",
            ColKind.SPARKLINE,
            help="The dataset's future count over the recent snapshots; a gap is omitted",
        ),
        Col("detail.dataset_url", "Dataset feed", ColKind.LINK, help="Opens the dataset feed"),
    ),
    filters=(FilterSpec("part.reason_label", "Reason"),),
    # Descending on the deficit is ascending on the delta: the feed deleting hardest relative
    # to what it refreshed is the one a steward is looking for, and it is the most negative
    # delta, not the largest one.
    sort_field="part.deficit_in_window",
    threshold_help=(
        "Show only the datasets the API has flagged past its own contact threshold."
    ),
    summary_field="detail.dataset_name",
    entity="dataset",
    email_fields=(
        ("Dataset", "detail.dataset_name"),
        ("Feeds", "detail.feed_count"),
        ("Endpoint", "detail.dataset_url"),
    ),
    query="monitor_dataset_future_decline_v1",
    page="views/23_dataset_future_decline.py",
    kpi_labels=("datasets declining", "publishers affected", "past threshold"),
)


FEED_QUALITY = Monitor(
    id="feed_quality",
    name="Feed data quality",
    group=Group.COVERAGE,
    severity=Severity.MEDIUM,
    # Not an incident list: the batch assesses every feed in the fleet each night and
    # reports the assessment, not a fault that has been open for N days.
    source=Source.QUALITY,
    blurb=(
        "The nightly quality assessment of every feed in the fleet: the status the crawl "
        "recorded, the quality score and grade where the assessment could produce one, how "
        "complete the recommended fields are, and how many future opportunities the feed "
        "carries. Rows are grouped by dataset and the datasets are ordered by their mean "
        "score, so a publisher's feeds are read together. The batch reports this snapshot "
        "only: there is no quality history behind it, so nothing on this page describes a "
        "trend."
    ),
    unit="average quality score",
    # The card is a summary rather than a count, and the monitor supplies its own figures
    # and verdict. See `monitors.quality.tile_card`.
    viz=Facts(),
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("dataset_name", "Dataset", ColKind.TEXT),
        Col("feed_name", "Feed", ColKind.MONO),
        Col("feed_type", "Type", ColKind.TEXT),
        Col("feed_version", "Version", ColKind.TEXT),
        Col("status", "Status", ColKind.STATUS),
        Col("grade", "Grade", ColKind.TEXT),
        Col(
            "score",
            "Score",
            ColKind.SCORE,
            help="The assessment's 0-100 quality score; blank where it could not score",
        ),
        Col(
            "completeness_percent",
            "Completeness",
            ColKind.PERCENT,
            help="Mean coverage of the recommended fields this assessment reported",
        ),
        Col("future_items", "Future items", ColKind.NUMBER),
        Col(
            "issue_count",
            "Issues",
            ColKind.NUMBER,
            help="Errors plus warnings; select a row to read them",
        ),
        Col("feed_url", "Endpoint", ColKind.LINK, help="Opens the publisher's feed endpoint"),
    ),
    filters=(
        FilterSpec("status", "Status"),
        FilterSpec("grade", "Grade"),
        FilterSpec("feed_type", "Feed type"),
        FilterSpec("feed_version", "Version"),
    ),
    # Datasets worst-last, and a dataset's own feeds kept together beneath it.
    sort_field="dataset_score",
    # Nothing here ages, so there is no contact threshold to filter on; the page offers an
    # "issues only" toggle in its place.
    has_threshold_filter=False,
    summary_field="feed_name",
    schedule="nightly assessment",
    query="monitor_feed_quality_v1",
    page="views/30_feed_quality.py",
    kpi_labels=("average quality score", "feeds scored", "feeds with errors"),
)


ACTIVE_PLACES_COVERAGE = Monitor(
    id="active_places_coverage",
    name="Active Places coverage",
    group=Group.COVERAGE,
    # Context, not a queue: nothing here is a fault a publisher is contacted about, so the
    # card stays grey. See `monitors.coverage.assess_coverage`.
    severity=Severity.INFORMATIONAL,
    # Neither an incident list nor a quality snapshot: the figures and the rows are two
    # endpoints, and neither has history. See `monitors.coverage`.
    source=Source.COVERAGE,
    rows_id="active_places_site_mappings",
    blurb=(
        "How much of the Active Places estate appears in the OpenActive data. A site counts "
        "as covered when an OpenActive venue sits within 200m of it, shares its postcode "
        "within 1km, or carries a clearly matching name within 500m. England only, every "
        "opportunity kind except Slot. The batch reports this snapshot only, so nothing "
        "on this page describes a trend. Method and caveats: "
        "[the Active Places reports]"
        "(https://github.com/openactive-contrib/openactive-monitor/tree/main/jobs/"
        "opportunity-insights/reports/active_places)."
    ),
    unit="of Active Places sites covered",
    # The card is a set of figures rather than a count, and the monitor supplies it along
    # with its verdict. See `monitors.coverage.tile_card`.
    viz=Facts(),
    columns=(
        Col("site_name", "Active Places site", ColKind.TEXT, primary=True),
        Col("local_authority_name", "Local authority", ColKind.TEXT),
        Col("postcode", "Postcode", ColKind.MONO),
        Col("ownership_type_group", "Ownership", ColKind.TEXT),
        Col("venue_name", "OpenActive venue", ColKind.TEXT),
        Col("publisher_names", "Publisher", ColKind.TEXT),
        Col("kinds", "Kinds", ColKind.TEXT),
        Col("opportunity_count", "Opportunities", ColKind.NUMBER),
        Col(
            "distance_metres",
            "Distance (m)",
            ColKind.NUMBER,
            help="Between the Active Places site and the matched OpenActive venue",
        ),
        Col("match_label", "Matched by", ColKind.TEXT),
        Col(
            "name_similarity_percent",
            "Name similarity",
            ColKind.PERCENT,
            help="Only the name channel reports one; blank where the match was spatial",
        ),
        Col(
            "dataset_url",
            "Dataset",
            ColKind.LINK,
            link_text="dataset \u2197",
            help="Opens the OpenActive dataset the matched venue was published in",
        ),
    ),
    filters=(
        FilterSpec("match_label", "Matched by"),
        FilterSpec("ownership_type_group", "Ownership"),
        FilterSpec("local_authority_name", "Local authority"),
    ),
    # The biggest venues first: a pair carrying hundreds of opportunities is the one worth
    # reading, and a site's own pairs stay together beneath it.
    sort_field="opportunity_count",
    # Nothing here ages, so there is no contact threshold to filter on; the page offers a
    # "primary pairs only" toggle in its place.
    has_threshold_filter=False,
    summary_field="site_name",
    schedule="daily · England, excluding Slot",
    query="active_places_coverage_v1",
    page="views/31_active_places_coverage.py",
)


FEED_CUSTOM_PROPERTIES = Monitor(
    id="feed_custom_properties",
    name="Schema drift",
    group=Group.COVERAGE,
    # Context, not a queue: the beta namespace is how the specification is meant to grow, so
    # a custom property is not a fault a publisher is contacted about. The card stays grey.
    # See `monitors.schema_drift.assess_drift`.
    severity=Severity.INFORMATIONAL,
    # Neither an incident list nor a quality snapshot: rows and a summary block in one read,
    # with nothing that ages and nothing scored. See `monitors.schema_drift`.
    source=Source.SCHEMA_DRIFT,
    blurb=(
        "Which feeds publish data beyond the OpenActive specification, and what that extra "
        "data is. It shows where publishers are extending the standard, which extensions "
        "are most widely used, and which datasets rely on them."
    ),
    unit="datasets using custom properties",
    # The card is a set of figures rather than a count, and the monitor supplies it along
    # with its verdict. See `monitors.schema_drift.tile_card`.
    viz=Facts(),
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("dataset_name", "Dataset", ColKind.TEXT),
        Col("feed_name", "Feed", ColKind.MONO),
        Col("feed_type", "Type", ColKind.TEXT),
        Col(
            "property_count",
            "Custom properties",
            ColKind.NUMBER,
            help="Distinct property names this feed uses outside the OpenActive vocabulary",
        ),
        Col(
            "property_label",
            "Properties",
            ColKind.TEXT,
            help="Every custom property this feed uses; hover a cell to read it in full",
        ),
        Col("namespace_label", "Namespaces", ColKind.TEXT),
        Col("feed_url", "Endpoint", ColKind.LINK, help="Opens the publisher's feed endpoint"),
    ),
    # List-valued fields filter by membership: a feed matches a namespace it uses anywhere.
    filters=(
        FilterSpec("namespaces", "Namespace"),
        FilterSpec("property_names", "Property"),
        FilterSpec("entity_types", "Entity type"),
        FilterSpec("feed_type", "Feed type"),
    ),
    sort_field="property_count",
    # Nothing here ages, so there is no contact threshold to filter on; the page offers an
    # "outside beta only" toggle in its place.
    has_threshold_filter=False,
    summary_field="feed_name",
    schedule="daily snapshot",
    query="monitor_feed_custom_properties_v1",
    page="views/32_feed_custom_properties.py",
)


#: Ordered registry. The overview and the sidebar iterate this — never a hard-coded list.
MONITOR_REGISTRY: tuple[Monitor, ...] = (
    DATASET_STALL,
    SINGLE_FEED_STALL,
    FEED_INGESTION_ERROR,
    DATASET_ORPHANED_CHILDREN,
    DATASET_FUTURE_DECLINE,
    FEED_QUALITY,
    ACTIVE_PLACES_COVERAGE,
    FEED_CUSTOM_PROPERTIES,
)

_BY_ID: Mapping[str, Monitor] = {m.id: m for m in MONITOR_REGISTRY}


def get_monitor(monitor_id: str) -> Monitor:
    try:
        return _BY_ID[monitor_id]
    except KeyError as exc:
        raise KeyError(f"unknown monitor {monitor_id!r}") from exc


def monitor_ids(source: Source | None = None) -> tuple[str, ...]:
    """Registered ids, in registry order; narrowed to one backing read where asked."""
    return tuple(m.id for m in MONITOR_REGISTRY if source is None or m.source is source)


def monitors_in_group(group: Group) -> Iterator[Monitor]:
    return (m for m in MONITOR_REGISTRY if m.group is group)


def groups() -> tuple[Group, ...]:
    """Groups that actually have monitors, in registry order."""
    seen: list[Group] = []
    for monitor in MONITOR_REGISTRY:
        if monitor.group not in seen:
            seen.append(monitor.group)
    return tuple(seen)
