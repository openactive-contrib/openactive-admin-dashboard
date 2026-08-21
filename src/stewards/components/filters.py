"""Filter controls. Options come from the snapshot; the filtering itself is pure."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import streamlit as st

from stewards.api.models import Incident
from stewards.monitors.registry import Monitor
from stewards.monitors.transforms import filter_options

ALL = "All"


@dataclass(frozen=True, slots=True)
class FilterState:
    search: str = ""
    past_threshold_only: bool = False
    selections: dict[str, str] = field(default_factory=dict)


def render_filters(monitor: Monitor, incidents: Sequence[Incident]) -> FilterState:
    """Search box, one selectbox per declared filter, and the threshold toggle."""
    widths = [2, *([1] * len(monitor.filters))]
    if monitor.has_threshold_filter:
        widths.append(1)
    columns = st.columns(widths, vertical_alignment="bottom")

    search = columns[0].text_input(
        "Search",
        key=f"search_{monitor.id}",
        placeholder="Filter publishers, feeds or types…",
        label_visibility="collapsed",
    )

    selections: dict[str, str] = {}
    for column, spec in zip(columns[1:], monitor.filters, strict=False):
        options = [ALL, *filter_options(monitor, incidents, spec.field)]
        chosen = column.selectbox(spec.label, options, key=f"filter_{monitor.id}_{spec.field}")
        selections[spec.field] = "" if chosen == ALL else chosen

    past_only = False
    if monitor.has_threshold_filter:
        past_only = columns[-1].toggle(
            "Past threshold only",
            value=False,
            key=f"threshold_{monitor.id}",
            help=f"Show only incidents open {monitor.threshold_days} days or more.",
        )

    return FilterState(search=search, past_threshold_only=past_only, selections=selections)
