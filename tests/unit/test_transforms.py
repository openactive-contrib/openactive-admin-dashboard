"""DataFrame shaping, RAG tones, KPIs and filtering."""

from __future__ import annotations

from datetime import date

import pytest

from stewards.api.models import (
    DetailModel,
    FeedIngestionErrorDetail,
    Incident,
    OrphanKind,
    StallDetail,
)
from stewards.monitors.registry import (
    Col,
    ColKind,
    FilterSpec,
    Group,
    Monitor,
    RowSpec,
    Severity,
)
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import (
    EMPTY,
    Kpi,
    Row,
    apply_filters,
    cell_tone,
    expand,
    field_total,
    filter_options,
    format_cell,
    incident_of,
    monitor_kpis,
    parse_detail,
    part_of,
    rag_columns,
    resolve_field,
    search_incidents,
    snapshot_total,
    sort_by_age,
    sort_rows,
    to_dataframe,
    tone_frame,
    unique_incidents,
)


def make_incident(**overrides: object) -> Incident:
    base: dict[str, object] = {
        "monitor_id": "single_feed_stall",
        "publisher_id": "pub_x",
        "publisher_name": "Publisher X",
        "feed_id": "feed_x",
        "feed_name": "scheduled-sessions",
        "feed_type": "ScheduledSession",
        "feed_url": "https://x.example.org/feed",
        "first_detected": "2026-08-14",
        "days_open": 7,
        "consecutive_days": 7,
        "past_threshold": True,
        "status": "contact_due",
        "trend": [1, 2, 3],
        "detail": {"last_modified": "2026-08-14"},
    }
    return Incident.model_validate(base | overrides)


# --- field resolution -----------------------------------------------------------------


def test_resolve_field_reads_a_plain_attribute(stall_monitor: Monitor) -> None:
    assert resolve_field(stall_monitor, make_incident(), "publisher_name") == "Publisher X"


def test_resolve_field_reads_through_the_detail_model(stall_monitor: Monitor) -> None:
    value = resolve_field(stall_monitor, make_incident(), "detail.last_modified")
    assert value == date(2026, 8, 14)


def test_resolve_field_returns_none_for_a_missing_detail_key(stall_monitor: Monitor) -> None:
    incident = make_incident(detail={})
    assert resolve_field(stall_monitor, incident, "detail.last_modified") is None


def test_resolve_field_returns_none_for_an_unknown_path(stall_monitor: Monitor) -> None:
    assert resolve_field(stall_monitor, make_incident(), "not_a_field") is None


def test_detail_model_ignores_keys_it_does_not_know(stall_monitor: Monitor) -> None:
    incident = make_incident(detail={"last_modified": "2026-08-14", "future_field": 12})
    detail = parse_detail(stall_monitor, incident)
    assert isinstance(detail, StallDetail)
    assert detail.last_modified == date(2026, 8, 14)


def test_detail_model_is_per_monitor(ingestion_monitor: Monitor) -> None:
    incident = make_incident(
        monitor_id="feed_ingestion_error",
        detail={
            "error_code": "503",
            "error_message": "HTTP 503 fetching https://example.org/openactive/sessions",
            "last_completed": "2026-08-10",
        },
    )
    detail = parse_detail(ingestion_monitor, incident)
    assert isinstance(detail, FeedIngestionErrorDetail)
    assert detail.error_code == "503"
    assert detail.error_message == "HTTP 503 fetching https://example.org/openactive/sessions"
    assert detail.last_completed == date(2026, 8, 10)


# --- cell formatting ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (ColKind.TEXT, "Freedom Leisure", "Freedom Leisure"),
        (ColKind.TEXT, None, EMPTY),
        (ColKind.TEXT, "", EMPTY),
        (ColKind.MONO, "scheduled-sessions", "scheduled-sessions"),
        (ColKind.NUMBER, 1204, 1204),
        (ColKind.NUMBER, None, None),
        (ColKind.DATE, date(2026, 8, 14), "2026-08-14"),
        (ColKind.DATE, None, EMPTY),
        (ColKind.DAYS, 22, "22d"),
        (ColKind.DAYS, 0, "0d"),
        (ColKind.DAYS, None, EMPTY),
        (ColKind.STATUS, "contact_due", "Contact due"),
        (ColKind.STATUS, None, EMPTY),
        (ColKind.SCORE, 82, 82.0),
        (ColKind.SCORE, None, None),
        (ColKind.SPARKLINE, (1, 2, 3), [1, 2, 3]),
        (ColKind.SPARKLINE, (), []),
        (ColKind.LINK, "https://x/feed", "https://x/feed"),
        (ColKind.LINK, None, None),
    ],
)
def test_format_cell(kind: ColKind, value: object, expected: object) -> None:
    assert format_cell(Col("f", "F", kind), value) == expected


# --- tones ----------------------------------------------------------------------------


def test_cell_tone_is_none_for_non_rag_columns() -> None:
    assert cell_tone(Col("feed_name", "Feed", ColKind.MONO), make_incident(), "x", 7) is None


def test_days_column_tone_uses_the_threshold() -> None:
    col = Col("days_open", "Days", ColKind.DAYS)
    assert cell_tone(col, make_incident(days_open=7), None, 7) is Tone.RED
    assert cell_tone(col, make_incident(days_open=6), None, 7) is Tone.AMBER


def test_status_column_tone_uses_the_status() -> None:
    col = Col("status", "Status", ColKind.STATUS)
    assert cell_tone(col, make_incident(status="new"), None, 7) is Tone.GREY


def test_score_column_tone_uses_the_value() -> None:
    col = Col("quality_score", "Score", ColKind.SCORE)
    assert cell_tone(col, make_incident(), 91, 7) is Tone.GREEN
    assert cell_tone(col, make_incident(), None, 7) is Tone.GREY


# --- frames ---------------------------------------------------------------------------


def test_dataframe_has_the_declared_columns_in_declared_order(stall_monitor: Monitor) -> None:
    frame = to_dataframe(stall_monitor, [make_incident()])
    assert list(frame.columns) == [c.label for c in stall_monitor.columns]
    assert len(frame) == 1


def test_dataframe_values_are_formatted_per_kind(stall_monitor: Monitor) -> None:
    row = to_dataframe(stall_monitor, [make_incident(days_open=22)]).iloc[0]
    assert row["Publisher"] == "Publisher X"
    assert row["Last modified"] == "2026-08-14"
    assert row["Days stalled"] == "22d"
    assert row["Status"] == "Contact due"
    assert row["Recent trend"] == [1, 2, 3]


def test_empty_input_gives_an_empty_frame_with_the_declared_columns(
    stall_monitor: Monitor,
) -> None:
    frame = to_dataframe(stall_monitor, [])
    assert frame.empty
    assert list(frame.columns) == [c.label for c in stall_monitor.columns]


def test_frame_row_count_matches_the_sample_payload(stall_monitor: Monitor, stall_page) -> None:
    assert len(to_dataframe(stall_monitor, stall_page.data)) == 23


def test_tone_frame_is_aligned_with_the_data_frame(stall_monitor: Monitor, stall_page) -> None:
    incidents = list(stall_page.data)
    frame = to_dataframe(stall_monitor, incidents)
    tones = tone_frame(stall_monitor, incidents)
    assert frame.shape == tones.shape
    assert list(frame.columns) == list(tones.columns)


def test_tone_frame_only_marks_rag_columns(stall_monitor: Monitor) -> None:
    tones = tone_frame(stall_monitor, [make_incident()])
    assert tones.iloc[0]["Days stalled"] == "red"
    assert tones.iloc[0]["Status"] == "red"
    assert tones.iloc[0]["Publisher"] == ""


def test_tone_frame_is_empty_for_no_incidents(stall_monitor: Monitor) -> None:
    assert tone_frame(stall_monitor, []).empty


def test_rag_columns_lists_only_shaded_labels(
    stall_monitor: Monitor, ingestion_monitor: Monitor
) -> None:
    assert rag_columns(stall_monitor) == ["Days stalled", "Status"]
    assert rag_columns(ingestion_monitor) == ["Consecutive failures"]


# --- KPIs -----------------------------------------------------------------------------


def test_kpis_count_incidents_publishers_and_past_threshold(stall_monitor: Monitor) -> None:
    incidents = [
        make_incident(days_open=22, past_threshold=True),
        make_incident(publisher_id="pub_x", days_open=3, past_threshold=False),
        make_incident(publisher_id="pub_y", days_open=9, past_threshold=True),
    ]
    count, publishers, past = monitor_kpis(stall_monitor, incidents)
    assert (count.value, publishers.value, past.value) == ("3", "2", "2")
    assert count.label == "feeds stalled"


def test_kpis_on_an_empty_snapshot_read_green(stall_monitor: Monitor) -> None:
    count, publishers, past = monitor_kpis(stall_monitor, [])
    assert (count.value, publishers.value, past.value) == ("0", "0", "0")
    assert count.tone is Tone.GREEN
    assert past.tone is Tone.GREEN
    assert isinstance(count, Kpi)


# --- search and filters ---------------------------------------------------------------


def test_search_is_case_insensitive_across_publisher_feed_and_type() -> None:
    incidents = [
        make_incident(),
        make_incident(publisher_name="Halo Leisure", feed_name="slots"),
    ]
    assert len(search_incidents(incidents, "halo")) == 1
    assert len(search_incidents(incidents, "SLOTS")) == 1
    assert len(search_incidents(incidents, "scheduledsession")) == 2
    assert len(search_incidents(incidents, "no-such-publisher")) == 0


def test_empty_search_returns_everything() -> None:
    incidents = [make_incident(), make_incident()]
    assert len(search_incidents(incidents, "   ")) == 2


def test_search_on_no_incidents_returns_empty() -> None:
    assert search_incidents([], "anything") == []


def test_filter_options_are_sorted_and_exclude_blanks(
    ingestion_monitor: Monitor, ingestion_page
) -> None:
    options = filter_options(ingestion_monitor, ingestion_page.data, "detail.error_code")
    assert options == sorted(options)
    assert "" not in options
    assert "503" in options


def test_filter_options_on_a_missing_field_is_empty(stall_monitor: Monitor) -> None:
    assert filter_options(stall_monitor, [make_incident(feed_type=None)], "feed_type") == []


def test_apply_filters_combines_search_selection_and_threshold(
    ingestion_monitor: Monitor, ingestion_page
) -> None:
    incidents = list(ingestion_page.data)
    only_503 = apply_filters(
        ingestion_monitor, incidents, selections={"detail.error_code": "503"}
    )
    assert {i.detail["error_code"] for i in only_503} == {"503"}

    past = apply_filters(ingestion_monitor, incidents, past_threshold_only=True)
    assert past
    assert all(i.past_threshold for i in past)

    both = apply_filters(
        ingestion_monitor,
        incidents,
        selections={"detail.error_code": "503"},
        past_threshold_only=True,
    )
    assert all(i.past_threshold and i.detail["error_code"] == "503" for i in both)


def test_apply_filters_ignores_a_blank_selection(
    ingestion_monitor: Monitor, ingestion_page
) -> None:
    incidents = list(ingestion_page.data)
    assert (
        apply_filters(ingestion_monitor, incidents, selections={"detail.error_code": ""})
        == incidents
    )


def test_apply_filters_on_no_incidents_returns_empty(ingestion_monitor: Monitor) -> None:
    assert apply_filters(ingestion_monitor, [], search="x", past_threshold_only=True) == []


def test_threshold_toggle_keeps_the_boundary_row(stall_monitor: Monitor) -> None:
    boundary = make_incident(days_open=7, past_threshold=True)
    assert apply_filters(stall_monitor, [boundary], past_threshold_only=True) == [boundary]


def test_sort_by_age_is_oldest_first_then_alphabetical() -> None:
    incidents = [
        make_incident(days_open=3, publisher_name="B"),
        make_incident(days_open=22, publisher_name="C"),
        make_incident(days_open=22, publisher_name="A"),
    ]
    assert [i.publisher_name for i in sort_by_age(incidents)] == ["A", "C", "B"]


def test_sort_by_age_of_nothing_is_nothing() -> None:
    assert sort_by_age([]) == []


# --- sparkline cells ----------------------------------------------------------------------


def test_a_sparkline_cell_drops_the_snapshots_with_no_figure() -> None:
    """The live API sends a null per missing snapshot; LineChartColumn needs numbers only.

    One null and the whole cell falls back to rendering the raw list as text, which is what
    the table showed before this rule.
    """
    col = Col("trend", "Recent trend", ColKind.SPARKLINE)
    assert format_cell(col, (1, 2, None, 4)) == [1.0, 2.0, 4.0]


def test_a_sparkline_cell_with_no_usable_points_is_empty() -> None:
    col = Col("trend", "Recent trend", ColKind.SPARKLINE)
    assert format_cell(col, ()) == []
    assert format_cell(col, (None, None)) == []


# --- the row: an incident, and the breakdown item it came from -----------------------------


def kind_monitor(**overrides: object) -> Monitor:
    """A throwaway monitor that explodes its incidents, for testing the mechanism itself."""
    base: dict[str, object] = {
        "id": "kinds",
        "name": "Kinds",
        "group": Group.CONTENT,
        "severity": Severity.MEDIUM,
        "blurb": "A" * 90,
        "unit": "things",
        "columns": (
            Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
            Col("part.kind", "Kind", ColKind.TEXT),
            Col("part.orphan_count", "Count", ColKind.NUMBER),
        ),
        "rows": RowSpec("detail.by_kind", OrphanKind),
        "detail_model": _ByKindDetail,
        "sort_field": "part.orphan_count",
    }
    return Monitor(**(base | overrides))  # type: ignore[arg-type]


class _ByKindDetail(DetailModel):
    by_kind: tuple[OrphanKind, ...] = ()


def kinded(**overrides: object) -> Incident:
    base: dict[str, object] = {
        "monitor_id": "kinds",
        "publisher_id": "pub_k",
        "publisher_name": "Publisher K",
        "past_threshold": False,
        "status": "open",
        "detail": {
            "by_kind": [
                {"kind": "Slot", "orphan_count": 5, "checked_count": 10},
                {"kind": "ScheduledSession", "orphan_count": 2, "checked_count": 10},
            ]
        },
    }
    return Incident.model_validate(base | overrides)


def test_a_monitor_with_no_row_spec_gets_one_row_per_incident(
    stall_monitor: Monitor, stall_page
) -> None:
    """The two shipped monitors must be untouched by the row machinery."""
    rows = expand(stall_monitor, stall_page.data)
    assert len(rows) == len(stall_page.data)
    assert all(row.part is None for row in rows)
    assert [row.incident for row in rows] == list(stall_page.data)


def test_a_row_spec_explodes_each_incident_into_its_breakdown() -> None:
    rows = expand(kind_monitor(), [kinded()])
    assert [row.part.kind for row in rows if row.part] == ["Slot", "ScheduledSession"]
    assert len({id(row.incident) for row in rows}) == 1


@pytest.mark.parametrize("detail", [{}, {"by_kind": []}, {"by_kind": None}])
def test_an_incident_with_no_breakdown_still_gets_its_own_row(detail: object) -> None:
    rows = expand(kind_monitor(), [kinded(detail=detail)])
    assert len(rows) == 1
    assert rows[0].part is None


def test_expanding_nothing_is_nothing() -> None:
    assert expand(kind_monitor(), []) == []


def test_a_part_field_reads_the_breakdown_and_a_bare_incident_reads_none() -> None:
    monitor = kind_monitor()
    row = expand(monitor, [kinded()])[0]
    assert resolve_field(monitor, row, "part.kind") == "Slot"
    assert resolve_field(monitor, row, "part.no_such_field") is None
    # A bare incident carries no part, so a part field is absent rather than an error.
    assert resolve_field(monitor, kinded(), "part.kind") is None


def test_the_row_helpers_accept_a_bare_incident() -> None:
    """Every caller with no breakdown — the contact queue, the registry tests — passes one."""
    incident = kinded()
    assert incident_of(incident) is incident
    assert part_of(incident) is None
    row = Row(incident, OrphanKind(kind="Slot"))
    assert incident_of(row) is incident
    assert part_of(row).kind == "Slot"


def test_unique_incidents_counts_a_dataset_once_however_many_rows_it_has() -> None:
    rows = expand(kind_monitor(), [kinded(), kinded(publisher_id="pub_j")])
    assert len(rows) == 4
    assert len(unique_incidents(rows)) == 2
    assert unique_incidents([]) == []


# --- ordering by the monitor's own field ---------------------------------------------------


def test_sort_rows_puts_the_largest_figure_first() -> None:
    monitor = kind_monitor()
    rows = sort_rows(monitor, expand(monitor, [kinded()]))
    assert [row.part.orphan_count for row in rows if row.part] == [5, 2]


def test_sort_rows_falls_back_to_the_publisher_name_when_the_field_ties() -> None:
    monitor = kind_monitor(sort_field="part.no_such_field")
    rows = expand(monitor, [kinded(publisher_name="Zed"), kinded(publisher_name="Alice")])
    ordered = sort_rows(monitor, rows)
    assert next(row.incident.publisher_name for row in ordered) == "Alice"


def test_sort_rows_on_days_open_matches_the_age_order(stall_monitor: Monitor) -> None:
    """The default sort field, so the two shipped monitors keep the order they had."""
    incidents = [
        make_incident(days_open=3, publisher_name="C"),
        make_incident(days_open=11, publisher_name="A"),
        make_incident(days_open=7, publisher_name="B"),
    ]
    by_age = [i.publisher_name for i in sort_by_age(incidents)]
    by_field = [
        r.incident.publisher_name
        for r in sort_rows(stall_monitor, expand(stall_monitor, incidents))
    ]
    assert by_age == ["A", "B", "C"]
    assert by_field == by_age


def test_a_monitor_reporting_no_age_sorts_last_rather_than_raising() -> None:
    """`days_open` is optional, and the contact queue sorts a mixed bag of monitors."""
    aged = make_incident(days_open=4, publisher_name="Aged")
    ageless = kinded(publisher_name="Ageless")
    assert [i.publisher_name for i in sort_by_age([ageless, aged])] == ["Aged", "Ageless"]


def test_a_null_days_open_carries_no_threshold_tone(stall_monitor: Monitor) -> None:
    """There is no age to shade against the threshold, so the cell is left unstyled."""
    col = Col("days_open", "Days stalled", ColKind.DAYS)
    assert cell_tone(col, kinded(), None, 7) is None
    assert format_cell(col, None) == EMPTY


# --- a headline figure that is a quantity, not a row count ---------------------------------


def test_field_total_sums_the_rows_that_report_the_field() -> None:
    monitor = kind_monitor()
    rows = expand(monitor, [kinded(), kinded(detail={})])
    assert field_total(monitor, rows, "part.orphan_count") == 7
    assert field_total(monitor, [], "part.orphan_count") == 0


def test_field_total_ignores_a_value_that_is_not_a_number() -> None:
    monitor = kind_monitor()
    rows = expand(monitor, [kinded()])
    assert field_total(monitor, rows, "part.kind") == 0
    # A bool is an int in Python and would silently count as one here.
    assert field_total(monitor, rows, "past_threshold") == 0


def test_the_headline_kpi_sums_where_the_monitor_declares_a_sum_field() -> None:
    monitor = kind_monitor(kpi_sum_field="part.orphan_count")
    rows = expand(monitor, [kinded()])
    assert monitor_kpis(monitor, rows)[0].value == "7"
    # Without the field it is a count of incidents, not of rows.
    assert monitor_kpis(kind_monitor(), rows)[0].value == "1"


def test_kpis_count_distinct_incidents_not_rows() -> None:
    monitor = kind_monitor()
    rows = expand(monitor, [kinded(), kinded(publisher_id="pub_j", past_threshold=True)])
    _, publishers, flagged = monitor_kpis(monitor, rows)
    assert len(rows) == 4
    assert publishers.value == "2"
    assert flagged.value == "1"


def test_the_snapshot_total_is_the_quantity_or_the_incident_count() -> None:
    rows = expand(kind_monitor(), [kinded(), kinded(publisher_id="pub_j")])
    assert snapshot_total(kind_monitor(kpi_sum_field="part.orphan_count"), rows) == 14
    assert snapshot_total(kind_monitor(), rows) == 2


def test_an_empty_snapshot_totals_zero() -> None:
    assert snapshot_total(kind_monitor(kpi_sum_field="part.orphan_count"), []) == 0
    assert snapshot_total(kind_monitor(), []) == 0


# --- filtering and searching over rows -----------------------------------------------------


def test_filters_and_options_read_a_breakdown_field() -> None:
    monitor = kind_monitor(filters=(FilterSpec("part.kind", "Kind"),))
    rows = expand(monitor, [kinded()])
    assert filter_options(monitor, rows, "part.kind") == ["ScheduledSession", "Slot"]
    kept = apply_filters(monitor, rows, selections={"part.kind": "Slot"})
    assert [row.part.kind for row in kept if row.part] == ["Slot"]


def test_search_matches_the_monitors_summary_field_when_given_the_monitor() -> None:
    monitor = kind_monitor(summary_field="part.kind")
    rows = expand(monitor, [kinded()])
    assert len(search_incidents(rows, "scheduledsession", monitor)) == 1
    # Without the monitor the summary field is not in the haystack.
    assert search_incidents(rows, "scheduledsession") == []


def test_search_without_a_term_keeps_every_row() -> None:
    monitor = kind_monitor()
    rows = expand(monitor, [kinded()])
    assert search_incidents(rows, "   ", monitor) == rows


def test_a_risk_column_shades_a_high_share_red() -> None:
    col = Col("part.orphan_percent", "Share", ColKind.RISK)
    assert cell_tone(col, kinded(), 90.0, 7) is Tone.RED
    assert cell_tone(col, kinded(), 1.0, 7) is Tone.GREEN
    assert cell_tone(col, kinded(), None, 7) is Tone.GREY
    assert format_cell(col, 90) == 90.0
    assert format_cell(col, None) is None


def test_a_risk_column_gets_a_rag_background() -> None:
    monitor = kind_monitor(
        columns=(
            Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
            Col("part.orphan_percent", "Share", ColKind.RISK),
        )
    )
    assert rag_columns(monitor) == ["Share"]
