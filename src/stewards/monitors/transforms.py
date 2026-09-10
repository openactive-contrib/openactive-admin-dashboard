"""Incidents -> DataFrames, KPIs and filters. No Streamlit, no I/O.

Everything a monitor page shows is derived here, driven entirely by the registry entry, so a
new monitor needs no change to this module.

The unit of a table is a `Row`: an incident, plus the breakdown item it was exploded from
when its monitor declares one. A monitor with no `rows` spec gets one row per incident and
behaves exactly as before, so `Row` costs the simple case nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from stewards.api.models import DetailModel, Incident
from stewards.monitors.registry import PART_PREFIX, RAG_KINDS, Col, ColKind, Monitor, RowDetail
from stewards.monitors.thresholds import (
    Tone,
    days_label,
    days_tone,
    risk_tone,
    score_tone,
    status_label,
    status_tone,
)

EMPTY = "—"

DETAIL_PREFIX = "detail."


@dataclass(frozen=True, slots=True)
class Row:
    """One table row: an incident, and the breakdown item it was exploded from, if any."""

    incident: Incident
    part: DetailModel | None = None


#: What the table functions read. Accepting a bare incident keeps every caller that has no
#: breakdown — the contact queue, the registry tests — working unchanged.
RowLike = Incident | Row


def incident_of(row: RowLike) -> Incident:
    return row.incident if isinstance(row, Row) else row


def part_of(row: RowLike) -> DetailModel | None:
    return row.part if isinstance(row, Row) else None


def parse_detail(monitor: Monitor, incident: Incident) -> DetailModel:
    """Validate an incident's monitor-specific `detail` blob.

    Unknown keys are ignored and missing keys default, so a detail field the API has not
    started sending yet renders as em dash rather than raising.
    """
    return monitor.detail_model.model_validate(incident.detail)


def expand(monitor: Monitor, incidents: Sequence[Incident]) -> list[Row]:
    """The table's rows. One per incident, or one per breakdown item where declared.

    An incident whose breakdown is absent or empty still yields its own row, so a dataset
    the batch reported without a breakdown is visible rather than dropped.
    """
    if monitor.rows is None:
        return [Row(incident) for incident in incidents]
    rows = []
    for incident in incidents:
        items = resolve_field(monitor, incident, monitor.rows.field) or ()
        parts = [
            monitor.rows.item_model.model_validate(item, from_attributes=True) for item in items
        ]
        rows.extend([Row(incident, part) for part in parts] or [Row(incident)])
    return rows


def resolve_field(monitor: Monitor, row: RowLike, path: str) -> Any:
    """Read `path` off a row.

    `detail.x` goes through the monitor's detail model, `part.x` through the breakdown item
    the row was exploded from, and anything else is an attribute of the incident.
    """
    incident = incident_of(row)
    if path.startswith(PART_PREFIX):
        name = path.removeprefix(PART_PREFIX)
        part = part_of(row)
        if part is not None:
            return getattr(part, name, None)
        # No breakdown on this row, so the same figure at dataset level answers for it. A
        # dataset the batch reported without a breakdown has exactly one row, so this
        # neither double-counts a total nor leaves its cells blank while it has a figure.
        return getattr(parse_detail(monitor, incident), name, None)
    if path.startswith(DETAIL_PREFIX):
        detail = parse_detail(monitor, incident)
        return getattr(detail, path.removeprefix(DETAIL_PREFIX), None)
    return getattr(incident, path, None)


def format_cell(col: Col, value: Any) -> Any:
    """Render one value for its column kind. Sparklines and numbers stay native."""
    match col.kind:
        case ColKind.SPARKLINE:
            # `st.column_config.LineChartColumn` needs a list of numbers: one null in the
            # series and the cell gives up and renders the raw list as text. A snapshot the
            # batch has no figure for is dropped from the line rather than drawn as a zero,
            # and a series with nothing left in it draws nothing at all.
            return [float(point) for point in value if point is not None] if value else []
        case ColKind.NUMBER:
            return None if value is None else int(value)
        case ColKind.PERCENT | ColKind.SCORE | ColKind.RISK:
            return None if value is None else float(value)
        case ColKind.DAYS:
            return EMPTY if value is None else days_label(int(value))
        case ColKind.DATE:
            return EMPTY if value is None else _iso(value)
        case ColKind.STATUS:
            return EMPTY if value is None else status_label(str(value))
        case ColKind.LINK:
            return value or None
        case _:
            return EMPTY if value in (None, "") else str(value)


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def cell_tone(col: Col, row: RowLike, value: Any, threshold_days: int) -> Tone | None:
    """RAG tone for a cell, or None when the column carries no RAG meaning."""
    if col.kind not in RAG_KINDS:
        return None
    incident = incident_of(row)
    match col.kind:
        case ColKind.DAYS:
            # A monitor measuring a snapshot reports no age; there is no threshold to shade
            # a missing figure against.
            days = incident.days_open
            return None if days is None else days_tone(days, threshold_days)
        case ColKind.STATUS:
            return status_tone(incident.status)
        case ColKind.RISK:
            return risk_tone(None if value is None else float(value))
        case _:
            return score_tone(None if value is None else float(value))


def to_dataframe(monitor: Monitor, rows: Sequence[RowLike]) -> pd.DataFrame:
    """One line per row, columns in the order the registry declares.

    Zero rows yields an empty frame with the declared columns, so the table renders empty
    instead of raising.
    """
    labels = [col.label for col in monitor.columns]
    records = [
        {
            col.label: format_cell(col, resolve_field(monitor, row, col.field))
            for col in monitor.columns
        }
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels)


def tone_frame(monitor: Monitor, rows: Sequence[RowLike]) -> pd.DataFrame:
    """Tone name per cell, aligned with `to_dataframe`; empty string where unstyled."""
    labels = [col.label for col in monitor.columns]
    records = [
        {
            col.label: (
                tone.value
                if (
                    tone := cell_tone(
                        col,
                        row,
                        resolve_field(monitor, row, col.field),
                        monitor.threshold_days,
                    )
                )
                else ""
            )
            for col in monitor.columns
        }
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels).fillna("")


def detail_items(monitor: Monitor, incident: Incident, spec: RowDetail) -> list[Row]:
    """The selected row's detail list, as rows its own columns can be read against."""
    items = resolve_field(monitor, incident, spec.field) or ()
    return [Row(incident, item) for item in items]


def detail_frame(monitor: Monitor, rows: Sequence[Row], spec: RowDetail) -> pd.DataFrame:
    """The row-detail table, formatted by the same column kinds as the main table."""
    labels = [col.label for col in spec.columns]
    records = [
        {
            col.label: format_cell(col, resolve_field(monitor, row, col.field))
            for col in spec.columns
        }
        for row in rows
    ]
    return pd.DataFrame(records, columns=labels)


def detail_caption(monitor: Monitor, incident: Incident, spec: RowDetail) -> str:
    """The caption above that table, with `{count}` filled from the declared count field."""
    if spec.count_field is None:
        return spec.caption
    total = resolve_field(monitor, incident, spec.count_field)
    return spec.caption.format(count=f"{total:,}" if isinstance(total, int) else EMPTY)


def rag_columns(monitor: Monitor) -> list[str]:
    """Labels of the columns that get a RAG background."""
    return [col.label for col in monitor.columns if col.kind in RAG_KINDS]


def unique_incidents(rows: Sequence[RowLike]) -> list[Incident]:
    """The distinct incidents behind a set of rows, in order.

    Identity, not equality: `expand` reuses one incident object across its breakdown rows,
    and `Incident` carries an untyped `detail` dict, so it is not hashable.
    """
    seen: set[int] = set()
    incidents = []
    for row in rows:
        incident = incident_of(row)
        if id(incident) not in seen:
            seen.add(id(incident))
            incidents.append(incident)
    return incidents


@dataclass(frozen=True, slots=True)
class Kpi:
    label: str
    value: str
    tone: Tone


def _numeric(value: Any) -> float | None:
    """The value as a number, or None. Bools are not numbers here, whatever Python says."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def field_total(monitor: Monitor, rows: Sequence[RowLike], path: str) -> float:
    """Sum a numeric field, skipping whatever does not report it.

    A breakdown field is summed per row; anything else is summed per distinct incident, or a
    dataset-level figure would be counted once for every row it exploded into.
    """
    summed: Sequence[RowLike] = rows if path.startswith(PART_PREFIX) else unique_incidents(rows)
    return sum(
        value for row in summed if (value := _numeric(resolve_field(monitor, row, path)))
    )


def snapshot_total(monitor: Monitor, rows: Sequence[RowLike]) -> int:
    """The monitor's own headline figure over these rows.

    The quantity it measures where it declares one, otherwise its incident count — the same
    figure `/summary` reports as this monitor's `count`.
    """
    if monitor.kpi_sum_field is None:
        return len(unique_incidents(rows))
    return int(field_total(monitor, rows, monitor.kpi_sum_field))


def monitor_kpis(monitor: Monitor, rows: Sequence[RowLike]) -> tuple[Kpi, Kpi, Kpi]:
    """The three metrics above every monitor table, derived from the filtered snapshot.

    Publishers and the past-threshold count are counted over distinct incidents, so a
    monitor whose incidents explode into several rows does not report each dataset twice.
    """
    incidents = unique_incidents(rows)
    past = sum(1 for i in incidents if i.past_threshold)
    publishers = len({i.publisher_id for i in incidents})
    if monitor.kpi_sum_field is not None:
        # A monitor that measures a quantity leads with the quantity, not the row count.
        total = field_total(monitor, rows, monitor.kpi_sum_field)
        headline = Kpi(
            monitor.kpi_labels[0] or monitor.unit,
            f"{total:,.0f}",
            Tone.RED if total else Tone.GREEN,
        )
    else:
        headline = Kpi(
            monitor.kpi_labels[0] or monitor.unit,
            f"{len(incidents):,}",
            Tone.RED if incidents else Tone.GREEN,
        )
    return (
        headline,
        Kpi(monitor.kpi_labels[1], f"{publishers:,}", Tone.GREY),
        Kpi(monitor.kpi_labels[2], f"{past:,}", Tone.RED if past else Tone.GREEN),
    )


def search_incidents[R: RowLike](
    rows: Iterable[R], term: str, monitor: Monitor | None = None
) -> list[R]:
    """Case-insensitive substring match on publisher name, feed name and feed type.

    Given the monitor, its summary field joins the haystack too, so a monitor identified by
    something other than a feed name — a dataset, say — is searchable by it.
    """
    needle = term.strip().lower()
    if not needle:
        return list(rows)
    matches = []
    for row in rows:
        incident = incident_of(row)
        parts = [incident.publisher_name, incident.feed_name or "", incident.feed_type or ""]
        if monitor is not None:
            parts.append(str(resolve_field(monitor, row, monitor.summary_field) or ""))
        if needle in " ".join(part.lower() for part in parts):
            matches.append(row)
    return matches


def filter_options(monitor: Monitor, rows: Sequence[RowLike], path: str) -> list[str]:
    """Distinct non-empty values for a filter field, sorted, for a selectbox."""
    values = {
        str(resolve_field(monitor, row, path))
        for row in rows
        if resolve_field(monitor, row, path) not in (None, "")
    }
    return sorted(values)


def apply_filters[R: RowLike](
    monitor: Monitor,
    rows: Sequence[R],
    *,
    search: str = "",
    selections: dict[str, str] | None = None,
    past_threshold_only: bool = False,
) -> list[R]:
    """Search, per-field selections and the threshold toggle, applied in that order.

    Filtering is local to the cached snapshot so the controls respond without a refetch.
    """
    result = search_incidents(rows, search, monitor)
    for path, wanted in (selections or {}).items():
        if not wanted:
            continue
        result = [r for r in result if str(resolve_field(monitor, r, path) or "") == wanted]
    if past_threshold_only:
        result = [r for r in result if incident_of(r).past_threshold]
    return result


def sort_rows[R: RowLike](monitor: Monitor, rows: Sequence[R]) -> list[R]:
    """Worst first on the monitor's own sort field, then publisher name.

    A row whose sort field is missing or not a number sorts as zero rather than raising, so
    a field the batch has stopped sending costs the table its order, not its rows.
    """

    def key(row: R) -> tuple[float, str]:
        value = _numeric(resolve_field(monitor, row, monitor.sort_field))
        return (-(value or 0.0), incident_of(row).publisher_name)

    return sorted(rows, key=key)


def sort_by_age[R: RowLike](rows: Iterable[R]) -> list[R]:
    """Oldest incident first — the order a steward works through.

    An incident whose monitor reports no age sorts as zero days, at the end.
    """

    def key(row: R) -> tuple[int, str]:
        incident = incident_of(row)
        return (-(incident.days_open or 0), incident.publisher_name)

    return sorted(rows, key=key)
