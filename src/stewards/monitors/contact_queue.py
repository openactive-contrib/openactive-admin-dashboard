"""The cross-monitor contact queue.

The API returns the union of every monitor's incidents at or past the threshold. This module
only shapes it: one row per incident per monitor, oldest first. An incident affecting the
same publisher under two monitors is two rows, by design — they are two conversations.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from stewards.api.models import Incident
from stewards.monitors.registry import (
    MONITOR_REGISTRY,
    RAG_KINDS,
    Col,
    ColKind,
    Monitor,
    get_monitor,
)
from stewards.monitors.thresholds import Tone, days_label, days_tone, status_label, status_tone
from stewards.monitors.transforms import EMPTY, resolve_field, sort_by_age

#: The queue's columns are fixed rather than per-monitor, but they are declared the same
#: way so `components.incident_table` can render them with no special case.
COLUMN_SPECS: tuple[Col, ...] = (
    Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
    Col("monitor_id", "Monitor", ColKind.TEXT),
    Col("detail", "Detail", ColKind.MONO, help="The monitor's summary field for this row"),
    Col("days_open", "Days open", ColKind.DAYS),
    Col("first_detected", "First detected", ColKind.DATE),
    Col("last_contacted", "Last contacted", ColKind.DATE),
    Col("status", "Status", ColKind.STATUS),
)

COLUMNS: tuple[str, ...] = tuple(col.label for col in COLUMN_SPECS)
RAG_COLUMNS: tuple[str, ...] = tuple(col.label for col in COLUMN_SPECS if col.kind in RAG_KINDS)


def _pairs(incidents: Sequence[Incident]) -> list[tuple[Monitor, Incident]]:
    """Queue rows as (monitor, incident), oldest first.

    Incidents from monitors this build does not register yet are dropped: this app ships a
    subset of the API's monitors, and a row with no column spec cannot be rendered honestly.
    """
    rows = []
    for incident in sort_by_age(incidents):
        try:
            rows.append((get_monitor(incident.monitor_id), incident))
        except KeyError:
            continue
    return rows


def known_incidents(incidents: Sequence[Incident]) -> list[Incident]:
    """The incidents this build can render, oldest first."""
    return [incident for _, incident in _pairs(incidents)]


def unknown_monitor_ids(incidents: Sequence[Incident]) -> list[str]:
    """Monitor ids the API reported that this build cannot render, for the page caption."""
    registered = {m.id for m in MONITOR_REGISTRY}
    return sorted({i.monitor_id for i in incidents if i.monitor_id not in registered})


def _detail(monitor: Monitor, incident: Incident) -> str:
    value = resolve_field(monitor, incident, monitor.summary_field)
    return str(value) if value not in (None, "") else EMPTY


def to_dataframe(incidents: Sequence[Incident]) -> pd.DataFrame:
    """Queue rows, oldest first."""
    rows = [
        {
            "Publisher": incident.publisher_name,
            "Monitor": monitor.name,
            "Detail": _detail(monitor, incident),
            "Days open": days_label(incident.days_open),
            "First detected": incident.first_detected.isoformat(),
            "Last contacted": (
                incident.last_contacted.isoformat() if incident.last_contacted else EMPTY
            ),
            "Status": status_label(incident.status),
        }
        for monitor, incident in _pairs(incidents)
    ]
    return pd.DataFrame(rows, columns=list(COLUMNS))


def tone_frame(incidents: Sequence[Incident]) -> pd.DataFrame:
    """Tone per cell for the RAG columns, aligned with `to_dataframe`."""
    rows = [
        {
            **dict.fromkeys(COLUMNS, ""),
            "Days open": days_tone(incident.days_open, monitor.threshold_days).value,
            "Status": status_tone(incident.status).value,
        }
        for monitor, incident in _pairs(incidents)
    ]
    return pd.DataFrame(rows, columns=list(COLUMNS))


def queue_kpis(incidents: Sequence[Incident]) -> tuple[tuple[str, str, Tone], ...]:
    """Incidents to contact, distinct publishers, and how many we have already written to."""
    rows = known_incidents(incidents)
    contacted = sum(1 for i in rows if i.last_contacted is not None)
    return (
        ("incidents to contact", f"{len(rows):,}", Tone.RED if rows else Tone.GREEN),
        ("distinct publishers", f"{len({i.publisher_id for i in rows}):,}", Tone.GREY),
        ("already contacted", f"{contacted:,}", Tone.AMBER if contacted else Tone.GREY),
    )
