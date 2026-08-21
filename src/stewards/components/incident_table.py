"""The incident table: column_config from ColKind, RAG shading from a Styler.

Generic over the registry — a new monitor never needs a change here. This module builds
widgets only; every value it renders was computed in `monitors.transforms`.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import Any

import pandas as pd
import streamlit as st
from pandas.io.formats.style import Styler

from stewards.components import theme
from stewards.monitors.registry import Col, ColKind, Monitor


def column_config(columns: Sequence[Col]) -> dict[str, Any]:
    """Map declared columns onto `st.column_config` entries."""
    config: dict[str, Any] = {}
    for col in columns:
        match col.kind:
            case ColKind.NUMBER:
                config[col.label] = st.column_config.NumberColumn(
                    col.label, format="%d", help=col.help
                )
            case ColKind.PERCENT | ColKind.SCORE:
                config[col.label] = st.column_config.ProgressColumn(
                    col.label, min_value=0, max_value=100, format="%d", help=col.help
                )
            case ColKind.SPARKLINE:
                config[col.label] = st.column_config.LineChartColumn(
                    col.label, y_min=0, width="small", help=col.help
                )
            case ColKind.LINK:
                config[col.label] = st.column_config.LinkColumn(
                    col.label, display_text="feed ↗", help=col.help
                )
            case _:
                config[col.label] = st.column_config.TextColumn(
                    col.label, help=col.help, width="medium" if col.primary else None
                )
    return config


def style_rag(
    frame: pd.DataFrame, tones: pd.DataFrame, rag_columns: Sequence[str]
) -> pd.DataFrame | Styler:
    """Background-shade the RAG columns only. Never colour a whole row."""
    shaded: list[Hashable] = [c for c in rag_columns if c in frame.columns]
    if not shaded or frame.empty:
        return frame
    aligned = tones.reindex(index=frame.index, columns=frame.columns, fill_value="")
    return frame.style.apply(
        lambda column: [theme.cell_css(tone) for tone in aligned[column.name]],
        subset=shaded,
    )


def render_table(
    frame: pd.DataFrame,
    tones: pd.DataFrame,
    *,
    columns: Sequence[Col],
    rag_columns: Sequence[str],
    key: str,
    empty_message: str,
) -> int | None:
    """Render the table and return the selected row position, if any."""
    if frame.empty:
        st.info(empty_message)
        return None

    event = st.dataframe(
        style_rag(frame, tones, rag_columns),
        column_config=column_config(columns),
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    rows = list(event.selection["rows"]) if event and event.selection else []
    return int(rows[0]) if rows else None


def render_monitor_table(
    monitor: Monitor, frame: pd.DataFrame, tones: pd.DataFrame, *, rag_columns: Sequence[str]
) -> int | None:
    return render_table(
        frame,
        tones,
        columns=monitor.columns,
        rag_columns=rag_columns,
        key=f"table_{monitor.id}",
        empty_message=(
            "No incidents match these filters in this snapshot. Clear the filters to see "
            "the full monitor."
        ),
    )
