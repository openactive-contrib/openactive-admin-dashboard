"""Incidents -> DataFrames, KPIs and filters. No Streamlit, no I/O.

Everything a monitor page shows is derived here, driven entirely by the registry entry, so a
new monitor needs no change to this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from stewards.api.models import DetailModel, Incident
from stewards.monitors.registry import RAG_KINDS, Col, ColKind, Monitor
from stewards.monitors.thresholds import (
    Tone,
    days_label,
    days_tone,
    score_tone,
    status_label,
    status_tone,
)

EMPTY = "—"


def parse_detail(monitor: Monitor, incident: Incident) -> DetailModel:
    """Validate an incident's monitor-specific `detail` blob.

    Unknown keys are ignored and missing keys default, so a detail field the API has not
    started sending yet renders as em dash rather than raising.
    """
    return monitor.detail_model.model_validate(incident.detail)


def resolve_field(monitor: Monitor, incident: Incident, path: str) -> Any:
    """Read `path` off an incident. `detail.x` goes through the monitor's detail model."""
    if path.startswith("detail."):
        return getattr(parse_detail(monitor, incident), path.removeprefix("detail."), None)
    return getattr(incident, path, None)


def format_cell(col: Col, value: Any) -> Any:
    """Render one value for its column kind. Sparklines and numbers stay native."""
    match col.kind:
        case ColKind.SPARKLINE:
            return list(value) if value else []
        case ColKind.NUMBER:
            return None if value is None else int(value)
        case ColKind.PERCENT | ColKind.SCORE:
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


def cell_tone(col: Col, incident: Incident, value: Any, threshold_days: int) -> Tone | None:
    """RAG tone for a cell, or None when the column carries no RAG meaning."""
    if col.kind not in RAG_KINDS:
        return None
    match col.kind:
        case ColKind.DAYS:
            return days_tone(incident.days_open, threshold_days)
        case ColKind.STATUS:
            return status_tone(incident.status)
        case _:
            return score_tone(None if value is None else float(value))


def to_dataframe(monitor: Monitor, incidents: Sequence[Incident]) -> pd.DataFrame:
    """One row per incident, columns in the order the registry declares.

    Zero incidents yields an empty frame with the declared columns, so the table renders
    empty instead of raising.
    """
    labels = [col.label for col in monitor.columns]
    rows = [
        {
            col.label: format_cell(col, resolve_field(monitor, incident, col.field))
            for col in monitor.columns
        }
        for incident in incidents
    ]
    return pd.DataFrame(rows, columns=labels)


def tone_frame(monitor: Monitor, incidents: Sequence[Incident]) -> pd.DataFrame:
    """Tone name per cell, aligned with `to_dataframe`; empty string where unstyled."""
    labels = [col.label for col in monitor.columns]
    rows = [
        {
            col.label: (
                tone.value
                if (
                    tone := cell_tone(
                        col,
                        incident,
                        resolve_field(monitor, incident, col.field),
                        monitor.threshold_days,
                    )
                )
                else ""
            )
            for col in monitor.columns
        }
        for incident in incidents
    ]
    return pd.DataFrame(rows, columns=labels).fillna("")


def rag_columns(monitor: Monitor) -> list[str]:
    """Labels of the columns that get a RAG background."""
    return [col.label for col in monitor.columns if col.kind in RAG_KINDS]


@dataclass(frozen=True, slots=True)
class Kpi:
    label: str
    value: str
    tone: Tone


def monitor_kpis(monitor: Monitor, incidents: Sequence[Incident]) -> tuple[Kpi, Kpi, Kpi]:
    """The three metrics above every monitor table, derived from the filtered snapshot."""
    past = sum(1 for i in incidents if i.past_threshold)
    publishers = len({i.publisher_id for i in incidents})
    return (
        Kpi(
            monitor.kpi_labels[0] or monitor.unit,
            f"{len(incidents):,}",
            Tone.RED if incidents else Tone.GREEN,
        ),
        Kpi(monitor.kpi_labels[1], f"{publishers:,}", Tone.GREY),
        Kpi(monitor.kpi_labels[2], f"{past:,}", Tone.RED if past else Tone.GREEN),
    )


def search_incidents(incidents: Iterable[Incident], term: str) -> list[Incident]:
    """Case-insensitive substring match on publisher name, feed name and feed type."""
    needle = term.strip().lower()
    if not needle:
        return list(incidents)
    return [
        i
        for i in incidents
        if needle
        in " ".join(
            part.lower() for part in (i.publisher_name, i.feed_name or "", i.feed_type or "")
        )
    ]


def filter_options(monitor: Monitor, incidents: Sequence[Incident], path: str) -> list[str]:
    """Distinct non-empty values for a filter field, sorted, for a selectbox."""
    values = {
        str(resolve_field(monitor, i, path))
        for i in incidents
        if resolve_field(monitor, i, path) not in (None, "")
    }
    return sorted(values)


def apply_filters(
    monitor: Monitor,
    incidents: Sequence[Incident],
    *,
    search: str = "",
    selections: dict[str, str] | None = None,
    past_threshold_only: bool = False,
) -> list[Incident]:
    """Search, per-field selections and the threshold toggle, applied in that order.

    Filtering is local to the cached snapshot so the controls respond without a refetch.
    """
    result = search_incidents(incidents, search)
    for path, wanted in (selections or {}).items():
        if not wanted:
            continue
        result = [i for i in result if str(resolve_field(monitor, i, path) or "") == wanted]
    if past_threshold_only:
        result = [i for i in result if i.past_threshold]
    return result


def sort_by_age(incidents: Iterable[Incident]) -> list[Incident]:
    """Oldest incident first — the order a steward works through."""
    return sorted(incidents, key=lambda i: (-i.days_open, i.publisher_name))
