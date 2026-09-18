"""The Active Places coverage snapshot -> rows, figures and a chart. No Streamlit, no I/O.

The third shaping module, beside `transforms.py` (incidents that age) and `quality.py` (a
fleet assessment). A coverage run answers a different question again: how much of an external
estate the OpenActive fleet reaches. Its figures and its rows arrive from two endpoints — one
object of totals, and a paginated list of site-venue pairs — and neither has history, so a
pair cannot be an `Incident` (no `first_detected`, no `days_open`, no `past_threshold`) and
the figures cannot be a quality `summary` block sitting beside its own rows.

What it shares is everything below the data: the registry declares the columns, the column
kinds format and shade the cells exactly as they do an incident table, the tones come from
`thresholds`, and the card's verdict is a `Health`, so the chip, the tone and the sidebar
pill run through the code every other monitor uses.

Pure, as `trend.py`, `gauge.py` and `quality.py` are: the chart builder takes its colours
from the caller rather than importing the theme.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import altair as alt
import pandas as pd

from stewards.api.models import CoverageGroup, CoverageSnapshot, SiteMapping
from stewards.monitors.health import Health, HealthState, Movement
from stewards.monitors.overview import Fact, TileCard
from stewards.monitors.quality import BAR_CORNER, CHART_HEIGHT
from stewards.monitors.registry import Col, ColKind, Monitor
from stewards.monitors.thresholds import Tone, risk_tone, score_tone
from stewards.monitors.transforms import EMPTY, format_cell

#: What a categorical field reads as when the batch reported none. A real string rather than
#: a blank, so it is a filter option a steward can actually select.
UNKNOWN = "Unknown"

#: Sorts a pair the batch reported no figure for after every pair that has one, without
#: conflating it with a genuine zero.
NO_FIGURE = -1.0

NOT_REPORTED = "coverage not reported in this snapshot"

#: How the batch spells each match channel, and how the page does. Declared rather than
#: de-slugged: "spatial_centroid_only" reads as prose only once someone writes the prose.
MATCH_LABELS: Mapping[str, str] = {
    "spatial": "Proximity",
    "spatial_and_postcode": "Proximity and postcode",
    "spatial_centroid_only": "Proximity, centroid only",
    "postcode": "Postcode",
    "name": "Name",
}


# --- rows ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MappingRow:
    """One site-venue pair as the table shows it, with the scalars the table needs derived.

    The API reports the OpenActive side as parallel lists — a venue cluster can carry several
    names, publishers and dataset URLs — and a table cell holds one value, so the lists are
    joined here and kept whole for the selected-row panel.
    """

    site_id: str
    site_name: str
    postcode: str
    local_authority_name: str
    ownership_type_group: str
    facility_count: int | None

    venue_name: str
    venue_names: tuple[str, ...]
    publisher_names: str
    publishers: tuple[str, ...]
    dataset_url: str
    dataset_urls: tuple[str, ...]
    venue_postcodes: tuple[str, ...]
    kinds: str
    kind_list: tuple[str, ...]
    opportunity_count: int | None

    distance_metres: float | None
    match_method: str
    match_label: str
    name_similarity_percent: float | None
    is_primary_for_venue: bool
    is_mutual_best: bool


def match_label(method: str) -> str:
    """`spatial_and_postcode` -> `Proximity and postcode`, and an unknown channel de-slugged
    rather than dropped: a channel the batch adds later should read as itself, not blank."""
    token = method.strip().lower()
    if not token:
        return UNKNOWN
    return MATCH_LABELS.get(token, token.replace("_", " ").capitalize())


def venue_label(mapping: SiteMapping) -> str:
    """The first name the matched venue cluster carries.

    A cluster with no name at all is common — a publisher that geocodes from a postcode
    often publishes no venue name — so this says so rather than reading as a gap in the app.
    """
    for name in mapping.oa_location_names:
        if name.strip():
            return name.strip()
    return "Unnamed venue"


def _join(values: Sequence[str]) -> str:
    """The distinct non-empty values, in order, as one cell."""
    seen: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return ", ".join(seen)


def as_percent(similarity: float | None) -> float | None:
    """A 0-1 similarity as a 0-100 figure, for a `PERCENT` column. None stays None: the
    channels that are not the name channel report no similarity, which is not a zero one."""
    return None if similarity is None else float(similarity) * 100.0


def build_rows(mappings: Sequence[SiteMapping]) -> tuple[MappingRow, ...]:
    """One row per site-venue pair the run matched."""
    return tuple(
        MappingRow(
            site_id=mapping.site_id,
            site_name=mapping.site_name.strip() or UNKNOWN,
            postcode=mapping.postcode.strip(),
            local_authority_name=mapping.local_authority_name.strip() or UNKNOWN,
            ownership_type_group=mapping.ownership_type_group.strip() or UNKNOWN,
            facility_count=mapping.ap_facility_count,
            venue_name=venue_label(mapping),
            venue_names=mapping.oa_location_names,
            publisher_names=_join(mapping.oa_publisher_names) or UNKNOWN,
            publishers=mapping.oa_publisher_names,
            dataset_url=mapping.oa_dataset_urls[0] if mapping.oa_dataset_urls else "",
            dataset_urls=mapping.oa_dataset_urls,
            venue_postcodes=mapping.oa_postal_codes,
            kinds=_join(mapping.oa_kinds) or UNKNOWN,
            kind_list=mapping.oa_kinds,
            opportunity_count=mapping.oa_opportunity_count,
            distance_metres=mapping.distance_metres,
            match_method=mapping.match_method,
            match_label=match_label(mapping.match_method),
            name_similarity_percent=as_percent(mapping.name_similarity),
            is_primary_for_venue=mapping.is_primary_for_venue,
            is_mutual_best=mapping.is_mutual_best,
        )
        for mapping in mappings
    )


def resolve(row: MappingRow, field: str) -> Any:
    """Read a declared column or filter field off a row. Flat by design, as on a quality
    row: every figure a mapping row reports is an attribute of it."""
    return getattr(row, field, None)


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def sort_rows(monitor: Monitor, rows: Sequence[MappingRow]) -> list[MappingRow]:
    """The monitor's `sort_field` descending, then the site, then the nearest pair first.

    Two pairs of the same site stay together beneath it, nearest first, so a site matched to
    several venues reads as one block rather than scattered through the table. A pair the
    batch reported no figure for sorts after every pair that has one.
    """

    def key(row: MappingRow) -> tuple[float, str, str, float]:
        figure = _numeric(resolve(row, monitor.sort_field))
        return (
            -(figure if figure is not None else NO_FIGURE),
            row.site_name.lower(),
            row.site_id,
            row.distance_metres if row.distance_metres is not None else float("inf"),
        )

    return sorted(rows, key=key)


# --- the table ----------------------------------------------------------------------------


def cell_tone(col: Col, value: Any) -> Tone | None:
    """RAG tone for a cell, or None where the column carries no RAG meaning.

    As on a quality row, every tone is read off the cell's own value: a mapping pair has no
    age to shade and no contact threshold to shade it against.
    """
    match col.kind:
        case ColKind.PERCENT | ColKind.SCORE:
            return score_tone(None if value is None else float(value))
        case ColKind.RISK:
            return risk_tone(None if value is None else float(value))
        case _:
            return None


def to_dataframe(monitor: Monitor, rows: Sequence[MappingRow]) -> pd.DataFrame:
    """One line per pair, columns in the order the registry declares.

    Zero rows yields an empty frame with the declared columns, so the table renders empty
    instead of raising.
    """
    labels = [col.label for col in monitor.columns]
    records = [
        {col.label: format_cell(col, resolve(row, col.field)) for col in monitor.columns}
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels)


def tone_frame(monitor: Monitor, rows: Sequence[MappingRow]) -> pd.DataFrame:
    """Tone name per cell, aligned with `to_dataframe`; empty string where unstyled."""
    labels = [col.label for col in monitor.columns]
    records = [
        {
            col.label: (tone.value if (tone := cell_tone(col, resolve(row, col.field))) else "")
            for col in monitor.columns
        }
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels).fillna("")


# --- filtering ----------------------------------------------------------------------------


def filter_options(rows: Sequence[MappingRow], field: str) -> list[str]:
    """Distinct non-empty values for a filter field, sorted, for a selectbox."""
    return sorted(
        {str(value) for row in rows if (value := resolve(row, field)) not in (None, "")}
    )


def search_rows(rows: Iterable[MappingRow], term: str) -> list[MappingRow]:
    """Case-insensitive substring match on site, postcode, local authority, venue and
    publisher — the five things a steward arrives at this table knowing."""
    needle = term.strip().lower()
    if not needle:
        return list(rows)
    return [
        row
        for row in rows
        if needle
        in " ".join(
            part.lower()
            for part in (
                row.site_name,
                row.postcode,
                row.local_authority_name,
                row.venue_name,
                row.publisher_names,
            )
        )
    ]


def apply_filters(
    rows: Sequence[MappingRow],
    *,
    search: str = "",
    selections: Mapping[str, str] | None = None,
    primary_only: bool = False,
) -> list[MappingRow]:
    """Search, per-field selections and the primary-pairs toggle, applied in that order.

    Local to the cached snapshot, as on every other page, so the controls respond without a
    refetch and stay unit-testable.
    """
    result = search_rows(rows, search)
    for field, wanted in (selections or {}).items():
        if not wanted:
            continue
        result = [row for row in result if str(resolve(row, field) or "") == wanted]
    if primary_only:
        result = [row for row in result if row.is_primary_for_venue]
    return result


# --- the figures --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stat:
    """One KPI block. `tone=None` leaves the value in body ink, for context rather than a
    state — the same bargain `layout.tone_metric` makes."""

    label: str
    value: str
    tone: Tone | None = None
    sub: str = ""


def format_number(count: int | None) -> str:
    """A count for display. None is a figure the batch did not report, never a zero."""
    return EMPTY if count is None else f"{count:,}"


def format_percent(value: float | None) -> str:
    return EMPTY if value is None else f"{value:.1f}%"


def page_stats(snapshot: CoverageSnapshot) -> tuple[Stat, ...]:
    """The four figures above the mapping table, straight off the snapshot's headline.

    None throughout rather than a tone: coverage is context, not a state to chase, and the
    card the overview draws says the same. Every figure reads em dash where the batch
    reported nothing.
    """
    head = snapshot.headline
    return (
        Stat(
            "Active Places coverage",
            format_percent(head.coverage_pct),
            None,
            f"of {format_number(head.sites_total)} sites in scope",
        ),
        Stat(
            "Sites matched",
            format_number(head.sites_matched),
            None,
            f"across {format_number(head.local_authorities)} local authorities",
        ),
        Stat(
            "Sites missing",
            format_number(head.sites_missing),
            None,
            "no OpenActive venue on any channel",
        ),
        Stat(
            "Venues not in Active Places",
            format_number(head.venues_unmatched),
            None,
            f"{format_percent(head.venues_unmatched_pct)} of "
            f"{format_number(head.venues_total)} venues",
        ),
    )


def assess_coverage(snapshot: CoverageSnapshot) -> Health:
    """The card's verdict.

    Deliberately not a RAG judgement. Coverage is a measurement of the estate, not a fault
    queue: there is no level at which somebody is contacted, and no defensible number to call
    26% good or bad against. The monitor therefore declares `Severity.INFORMATIONAL`, and
    `overview.tile_state` / `tile_label` render a warning on such a monitor as a grey "Info"
    chip — the one place severity changes behaviour, and exactly what this monitor wants. Do
    not "fix" this to `HEALTHY`: that would claim an all-clear the figure cannot support.

    `movement` stays unknown for the reason `gauge.assess_benchmark` and
    `quality.assess_quality` give: one snapshot carries a level and says nothing about
    direction. There is no coverage history behind this read.
    """
    coverage_pct = snapshot.headline.coverage_pct
    if coverage_pct is None:
        return Health(HealthState.UNKNOWN, Movement.UNKNOWN, NOT_REPORTED)
    reason = (
        f"{format_percent(coverage_pct)} of "
        f"{format_number(snapshot.headline.sites_total)} Active Places sites "
        "appear in the OpenActive data"
    )
    return Health(HealthState.WARNING, Movement.UNKNOWN, reason, current=coverage_pct)


def tile_note(snapshot: CoverageSnapshot) -> str:
    head = snapshot.headline
    if head.coverage_pct is None:
        return NOT_REPORTED
    return (
        f"{format_number(head.sites_total)} sites across "
        f"{format_number(head.local_authorities)} local authorities · this snapshot only"
    )


def tile_card(snapshot: CoverageSnapshot) -> TileCard:
    """The overview card: the coverage rate, three supporting figures and the verdict.

    `badge=None`: the sidebar pill counts what needs working through, and a coverage gap is
    not a queue. See `tile_viz.Facts`.
    """
    head = snapshot.headline
    return TileCard(
        value=format_percent(head.coverage_pct),
        unit="of Active Places sites covered",
        health=assess_coverage(snapshot),
        facts=(
            Fact("Sites matched", format_number(head.sites_matched)),
            Fact("Sites missing", format_number(head.sites_missing)),
            Fact("Venues not in Active Places", format_number(head.venues_unmatched)),
        ),
        note=tile_note(snapshot),
        badge=None,
    )


# --- the selected row ---------------------------------------------------------------------


def pair_facts(row: MappingRow) -> tuple[tuple[str, str], ...]:
    """Label and value per line of the selected pair's panel, blanks dropped."""
    lines = (
        ("Matched by", row.match_label),
        (
            "Distance",
            EMPTY if row.distance_metres is None else f"{row.distance_metres:,.0f} m",
        ),
        (
            "Name similarity",
            EMPTY
            if row.name_similarity_percent is None
            else f"{row.name_similarity_percent:.0f}%",
        ),
        ("Active Places facilities", format_number(row.facility_count)),
        ("Opportunities at this venue", format_number(row.opportunity_count)),
        ("Venue names", _join(row.venue_names) or EMPTY),
        ("Venue postcodes", _join(row.venue_postcodes) or EMPTY),
        ("Publishers", _join(row.publishers) or EMPTY),
        ("Opportunity kinds", _join(row.kind_list) or EMPTY),
        (
            "Primary pair for this venue",
            "yes" if row.is_primary_for_venue else "no",
        ),
        ("Mutually nearest", "yes" if row.is_mutual_best else "no"),
    )
    return tuple((label, value) for label, value in lines if value)


# --- the summary chart --------------------------------------------------------------------


def region_chart(
    snapshot: CoverageSnapshot,
    colour: str,
    *,
    label_colour: str,
    grid_colour: str,
    height: int = CHART_HEIGHT + 60,
) -> alt.Chart | None:
    """Coverage rate per English region, best first.

    One flat colour, for the reason `quality.grade_chart` gives: a region's coverage rate is
    a measurement, not a RAG verdict, and shading every region red because the whole estate
    sits at 26% would claim a judgement the analysis does not make. None for an empty
    breakdown, matching every other builder, so the caller draws nothing rather than an empty
    axis.
    """
    reported: list[CoverageGroup] = [
        group
        for group in snapshot.coverage_by_region
        if group.coverage_pct is not None and group.region_name.strip()
    ]
    if not reported:
        return None
    reported.sort(key=lambda group: group.coverage_pct or 0.0, reverse=True)

    frame = pd.DataFrame(
        {
            "label": [group.region_name for group in reported],
            "value": [float(group.coverage_pct or 0.0) for group in reported],
            "sites": [group.sites_total or 0 for group in reported],
            "matched": [group.sites_matched or 0 for group in reported],
        }
    )
    chart: alt.Chart = (
        alt.Chart(frame)
        .mark_bar(
            color=colour, cornerRadiusTopRight=BAR_CORNER, cornerRadiusBottomRight=BAR_CORNER
        )
        .encode(
            y=alt.Y(
                "label:N",
                title=None,
                sort=None,
                axis=alt.Axis(labelLimit=180, grid=False, labelAngle=0),
            ),
            x=alt.X("value:Q", title="Coverage %", axis=alt.Axis(tickCount=4, grid=True)),
            tooltip=[
                alt.Tooltip("label:N", title="Region"),
                alt.Tooltip("value:Q", title="Coverage %", format=",.1f"),
                alt.Tooltip("matched:Q", title="Sites matched", format=","),
                alt.Tooltip("sites:Q", title="Sites in scope", format=","),
            ],
        )
        .properties(height=height)
        .configure_view(strokeWidth=0, fill=None)
        .configure(background="transparent")
        .configure_axis(
            domain=False,
            labelColor=label_colour,
            titleColor=label_colour,
            tickColor=grid_colour,
            gridColor=grid_colour,
        )
    )
    return chart
