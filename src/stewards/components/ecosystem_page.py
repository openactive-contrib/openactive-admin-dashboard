"""The ecosystem growth page: header, blurb, a chart with a series picker, the monthly table.

Not a monitor page. The growth series judges nothing, so there is no KPI row, no filter, no
email draft and no card on the overview; the page lives in the sidebar's Ecosystem section,
which `nav.build_navigation` builds outside the monitor registry.

A thin renderer, as the hard rules require: every frame and the chart come from
`monitors.ecosystem`. The table keeps to counts; selecting a month opens the datasets behind
them in the app's usual row pop-up (`row_dialog`), one tab per list.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import EcosystemGrowthPeriod
from stewards.components import layout, theme
from stewards.components.errors import render_api_error
from stewards.components.incident_table import column_config, render_table
from stewards.components.loading import loading
from stewards.components.row_dialog import render_row_dialog
from stewards.components.surface import card
from stewards.monitors import ecosystem

CRUMB = "Ecosystem"
TITLE = "Growth"
KEY = "ecosystem_growth"

#: One colour per series, from the palette the rest of the app draws with. A pair sharing a
#: colour is told apart by the dashed line its `Series` declares.
SERIES_COLOURS: dict[str, str] = {
    "datasets_crawled": theme.TEAL,
    "datasets_added": theme.TEAL_HOVER,
    "datasets_removed": theme.MUTED,
    "datasets_with_future": theme.GREEN,
    "datasets_without_future": theme.AMBER,
    "datasets_not_ingested": theme.AMBER,
    "datasets_stopped": theme.RED,
    "datasets_stopped_all_runs_failed": theme.RED,
}


def render_blurb() -> None:
    with card(f"blurb_{KEY}"):
        st.markdown(ecosystem.BLURB)


def render_chart(periods: Sequence[EcosystemGrowthPeriod]) -> None:
    with card(f"chart_{KEY}"):
        st.subheader("Monthly trend", anchor=False, divider=False)
        fields = st.multiselect(
            "Series",
            [s.field for s in ecosystem.SERIES],
            default=list(ecosystem.DEFAULT_SERIES),
            format_func=ecosystem.label_for,
            key=f"series_{KEY}",
        )
        if not fields:
            st.caption("Choose at least one series to draw the chart.")
            return
        chart = ecosystem.growth_chart(
            periods,
            fields,
            colours=SERIES_COLOURS,
            label_colour=theme.MUTED,
            grid_colour=theme.BORDER_SUBTLE,
            surface_colour=theme.SURFACE,
        )
        if chart is None:
            st.caption("This snapshot reports no figures for the chosen series.")
            return
        st.altair_chart(chart, width="stretch")
        st.caption(
            "A hollow point marks a month still in progress. A month the batch did not "
            "compute a figure for is left out rather than drawn as zero."
        )


def render_month_detail(period: EcosystemGrowthPeriod) -> None:
    """The datasets behind a month's figures, one tab per list. Shown in the row pop-up."""
    st.caption(ecosystem.dialog_caption(period))
    series = ecosystem.LISTED_SERIES
    tabs = st.tabs([ecosystem.list_tab_label(period, s) for s in series])
    for tab, item in zip(tabs, series, strict=True):
        with tab:
            datasets = ecosystem.dataset_list(period, item)
            if datasets is None:
                st.caption(ecosystem.NOT_COMPUTED)
            elif not datasets:
                st.caption(ecosystem.NONE_THIS_MONTH)
            else:
                st.dataframe(
                    ecosystem.detail_frame(datasets),
                    column_config=column_config(ecosystem.DETAIL_COLUMNS),
                    hide_index=True,
                    width="stretch",
                    key=f"datasets_{KEY}_{ecosystem.month_label(period)}_{item.field}",
                )


def render_months(periods: Sequence[EcosystemGrowthPeriod]) -> None:
    """The monthly table, and the selected month's datasets in a pop-up."""
    table_key = f"months_{KEY}"
    with card(f"table_{KEY}"):
        st.subheader("By month", anchor=False, divider=False)
        frame = ecosystem.table_frame(periods)
        if not frame.empty:
            st.caption(
                f"{len(frame):,} months, most recent first. "
                "Select a month to see the datasets behind its figures."
            )
        selected = render_table(
            frame,
            pd.DataFrame(),
            columns=ecosystem.TABLE_COLUMNS,
            rag_columns=(),
            key=table_key,
            empty_message="This snapshot reports no months.",
        )
    # The rows in the table's own order, so a selected position finds its month.
    render_row_dialog(
        ecosystem.newest_first(periods),
        ecosystem.month_label,
        selected,
        key=f"dialog_{KEY}",
        table_key=table_key,
        title=ecosystem.dialog_title,
        render=render_month_detail,
    )


def render_ecosystem_growth_page() -> None:
    try:
        with loading("Loading ecosystem growth"):
            response = repository.fetch_ecosystem_growth()
    except ApiError as exc:
        layout.render_error_header(CRUMB, TITLE)
        render_api_error(exc)
        return

    layout.render_header(CRUMB, TITLE, response.meta)
    render_blurb()
    render_chart(response.data)
    render_months(response.data)
    layout.render_footer("", actions="none")
