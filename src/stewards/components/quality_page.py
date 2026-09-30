"""The whole of a quality monitor's page.

`monitor_page.py` renders a list of faults that age — header, blurb, three KPIs, trend,
filters, table, email draft. A quality monitor reports something else: the assessment of
every feed in the fleet in one snapshot, with a summary block beside it and no history. So
the page is the same shape read differently — header, blurb, five figures, the summary
charts in place of the trend, filters, the table grouped by dataset — and everything it
shows was computed in `monitors.quality`.

A thin renderer, as the hard rules require: no arithmetic here, and every column, filter and
label comes off the registry entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import altair as alt
import streamlit as st

from stewards.api import repository
from stewards.api.errors import ApiError
from stewards.api.models import FeedQualitySummary
from stewards.components import layout, theme
from stewards.components.errors import render_api_error
from stewards.components.filters import ALL, FilterState
from stewards.components.incident_table import render_table
from stewards.components.loading import loading
from stewards.components.surface import card
from stewards.monitors import quality, transforms
from stewards.monitors.quality import QualityRow
from stewards.monitors.registry import Monitor

CHARTS_PER_ROW = 2


def render_blurb(monitor: Monitor) -> None:
    with card(f"blurb_{monitor.id}"):
        st.markdown(monitor.blurb)
        st.caption(" · ".join(f"`{chip}`" for chip in monitor.meta_chips))


def render_stats(monitor: Monitor, summary: FeedQualitySummary) -> None:
    """The snapshot's own figures, straight off the summary block.

    Deliberately not recomputed from the filtered table: these describe the fleet the batch
    assessed, and a filter narrowing the rows below must not appear to move them.
    """
    stats = quality.page_stats(summary)
    for index, (column, stat) in enumerate(zip(st.columns(len(stats)), stats, strict=True)):
        with column, card(f"kpi_{monitor.id}_{index}"):
            layout.tone_metric(
                stat.label,
                stat.value,
                stat.tone,
                slug=f"{monitor.id}{index}",
                sub=stat.sub,
            )


def render_chart(title: str, caption: str, chart: alt.Chart | None, key: str) -> None:
    with card(key):
        st.subheader(title, anchor=False, divider=False)
        if chart is None:
            st.caption("This snapshot does not report the figures for this chart.")
            return
        st.altair_chart(chart, width="stretch")
        st.caption(caption)


def render_summary_charts(monitor: Monitor, summary: FeedQualitySummary) -> None:
    """Four views of the same snapshot: how scores are spread, which fields are thin, and
    how the fleet splits by crawl status and by grade.

    Altair rather than `st.bar_chart` for the reason the trend chart uses it: a per-bar
    colour and a transparent plot area, so the RAG shading matches the table and the chart
    sits on the white card rather than in a grey panel.
    """
    charts = (
        (
            "Quality score distribution",
            "Scored feeds per score band. Bands are shaded on the same scale as the "
            "Score column below.",
            quality.score_distribution_chart(
                summary,
                theme.FOREGROUND,
                label_colour=theme.MUTED,
                grid_colour=theme.BORDER_SUBTLE,
            ),
        ),
        (
            "Recommended field completeness",
            "Mean coverage of each recommended field across the feeds that reported it.",
            quality.completeness_chart(
                summary,
                theme.FOREGROUND,
                label_colour=theme.MUTED,
                grid_colour=theme.BORDER_SUBTLE,
            ),
        ),
        (
            "Feeds by crawl status",
            "What the nightly crawl recorded against each feed.",
            quality.status_chart(
                summary,
                theme.FOREGROUND,
                label_colour=theme.MUTED,
                grid_colour=theme.BORDER_SUBTLE,
            ),
        ),
        (
            "Feeds by quality grade",
            "The grade the assessment awarded. A feed it could not grade is counted as "
            "unknown rather than left out.",
            quality.grade_chart(
                summary,
                theme.TEAL,
                label_colour=theme.MUTED,
                grid_colour=theme.BORDER_SUBTLE,
            ),
        ),
    )
    for start in range(0, len(charts), CHARTS_PER_ROW):
        row = charts[start : start + CHARTS_PER_ROW]
        for index, (column, (title, caption, chart)) in enumerate(
            zip(st.columns(CHARTS_PER_ROW), row, strict=False)
        ):
            with column:
                render_chart(title, caption, chart, f"chart_{monitor.id}_{start + index}")


def render_filters(monitor: Monitor, rows: Sequence[QualityRow]) -> FilterState:
    """Search, one selectbox per declared filter, and the issues toggle.

    The toggle stands where a monitor page puts "past threshold only": nothing in a quality
    snapshot ages, so the useful narrowing is to the feeds the assessment actually flagged.
    """
    columns = st.columns([2, *([1] * len(monitor.filters)), 1], vertical_alignment="bottom")

    search = columns[0].text_input(
        "Search",
        key=f"search_{monitor.id}",
        placeholder="Filter publishers, datasets or feeds…",
        label_visibility="collapsed",
    )

    selections: dict[str, str] = {}
    for column, spec in zip(columns[1:], monitor.filters, strict=False):
        options = [ALL, *quality.filter_options(rows, spec.field)]
        chosen = column.selectbox(spec.label, options, key=f"filter_{monitor.id}_{spec.field}")
        selections[spec.field] = "" if chosen == ALL else chosen

    issues_only = columns[-1].toggle(
        "Issues only",
        value=False,
        key=f"issues_{monitor.id}",
        help="Show only feeds the assessment recorded an error or a warning against.",
    )
    return FilterState(search=search, past_threshold_only=issues_only, selections=selections)


def render_row_detail(monitor: Monitor, row: QualityRow) -> None:
    """What the assessment said about the selected feed: its issues, the required fields it
    is missing, and its coverage per recommended field."""
    with st.expander(f"Assessment · {row.feed_name}", expanded=True):
        st.caption(
            f"{row.publisher_name} · {row.dataset_name} · {row.feed_type} · "
            f"{row.regularity.lower()} · assessed {row.last_assessed}"
        )

        issues = quality.issue_lines(row)
        missing = quality.missing_field_lines(row)
        if issues:
            for kind, text in issues:
                st.markdown(f"**{kind}** — {text}")
        else:
            st.markdown("No errors or warnings recorded against this feed.")
        if missing:
            st.markdown("**Missing required fields**")
            for line in missing:
                st.markdown(f"- {line}")

        frame = quality.completeness_frame(row)
        if frame.empty:
            st.caption("This snapshot reports no field completeness for this feed.")
            return
        st.dataframe(
            frame,
            column_config={
                "Coverage": st.column_config.ProgressColumn(
                    "Coverage", min_value=0, max_value=100, format="%d"
                )
            },
            hide_index=True,
            width="stretch",
            key=f"completeness_{monitor.id}",
        )


def render_quality_page(monitor: Monitor) -> None:
    """Header, blurb, figures, summary charts, filters, table, selected assessment, footer."""
    try:
        with loading("Loading the quality snapshot"):
            response = repository.fetch_quality(monitor.id)
    except ApiError as exc:
        layout.render_error_header(monitor.crumb, monitor.name)
        render_api_error(exc)
        return

    layout.render_header(monitor.crumb, monitor.name, response.meta)
    render_blurb(monitor)
    render_stats(monitor, response.summary)
    render_summary_charts(monitor, response.summary)

    rows = quality.build_rows(response.data)
    state = render_filters(monitor, rows)
    shown = quality.sort_rows(
        monitor,
        quality.apply_filters(
            rows,
            search=state.search,
            selections=state.selections,
            issues_only=state.past_threshold_only,
        ),
    )

    st.caption(
        f"{len(shown):,} of {len(rows):,} feeds shown. Feeds are grouped by dataset and the "
        "datasets ordered by their mean quality score, highest first. Select a row to read "
        "its assessment."
    )
    selected = render_table(
        quality.to_dataframe(monitor, shown),
        quality.tone_frame(monitor, shown),
        columns=monitor.columns,
        rag_columns=transforms.rag_columns(monitor),
        key=f"table_{monitor.id}",
        empty_message=(
            "No feeds match these filters in this snapshot. Clear the filters to see the "
            "whole fleet."
        ),
    )
    if selected is not None and selected < len(shown):
        render_row_detail(monitor, shown[selected])

    layout.render_footer(monitor.query)
