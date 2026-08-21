"""Column config and RAG styling.

Both are pure mappings — `st.column_config.*` builds a spec object and needs no runtime —
so they are tested here rather than through a page render.
"""

from __future__ import annotations

import pandas as pd

from stewards.components import theme
from stewards.components.incident_table import column_config, style_rag
from stewards.monitors.registry import Col, ColKind, get_monitor

ALL_KINDS = [
    Col("a", "Text", ColKind.TEXT),
    Col("b", "Mono", ColKind.MONO),
    Col("c", "Count", ColKind.NUMBER),
    Col("d", "Date", ColKind.DATE),
    Col("e", "Days", ColKind.DAYS),
    Col("f", "Match %", ColKind.PERCENT),
    Col("g", "Score", ColKind.SCORE),
    Col("h", "Trend", ColKind.SPARKLINE),
    Col("i", "Status", ColKind.STATUS),
    Col("j", "Endpoint", ColKind.LINK),
]


def test_every_column_kind_maps_to_a_widget() -> None:
    config = column_config(ALL_KINDS)
    assert set(config) == {col.label for col in ALL_KINDS}
    assert all(spec is not None for spec in config.values())


def test_no_columns_gives_no_config() -> None:
    assert column_config([]) == {}


def test_registry_columns_all_map() -> None:
    for monitor_id in ("single_feed_stall", "http_failure"):
        monitor = get_monitor(monitor_id)
        assert len(column_config(monitor.columns)) == len(monitor.columns)


def test_styler_shades_only_the_named_columns() -> None:
    frame = pd.DataFrame([{"Publisher": "X", "Days stalled": "22d"}])
    tones = pd.DataFrame([{"Publisher": "", "Days stalled": "red"}])
    styled = style_rag(frame, tones, ["Days stalled"])
    html = styled.to_html()  # type: ignore[union-attr]
    assert theme.RED_BG in html
    assert theme.RED in html
    # One shaded cell only: the Publisher column must not be coloured.
    assert html.count("background-color") == 1


def test_an_empty_frame_is_returned_unstyled() -> None:
    frame = pd.DataFrame(columns=["Publisher", "Days stalled"])
    result = style_rag(frame, pd.DataFrame(), ["Days stalled"])
    assert isinstance(result, pd.DataFrame)


def test_a_frame_with_no_rag_columns_is_returned_unstyled() -> None:
    frame = pd.DataFrame([{"Publisher": "X"}])
    assert isinstance(
        style_rag(frame, pd.DataFrame([{"Publisher": ""}]), ["Days stalled"]), pd.DataFrame
    )


def test_a_missing_tone_leaves_the_cell_unstyled() -> None:
    frame = pd.DataFrame([{"Days stalled": "3d"}])
    styled = style_rag(frame, pd.DataFrame([{"Days stalled": ""}]), ["Days stalled"])
    assert "background-color" not in styled.to_html()  # type: ignore[union-attr]


def test_tones_missing_a_column_are_filled_rather_than_raising() -> None:
    frame = pd.DataFrame([{"Publisher": "X", "Days stalled": "22d"}])
    styled = style_rag(frame, pd.DataFrame([{"Days stalled": "red"}]), ["Days stalled"])
    assert theme.RED_BG in styled.to_html()  # type: ignore[union-attr]
