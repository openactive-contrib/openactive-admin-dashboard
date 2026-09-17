"""The feed quality monitor: rows, dataset grouping, figures and charts.

The snapshot this monitor reports has no history and nothing that ages, so the checks worth
making are different from an incident monitor's: that a dataset's feeds stay together and in
the right order, that an unscored feed is not treated as a zero, and that a figure the batch
did not report reads as em dash rather than as an all-clear.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from fixture_loader import load_sample
from stewards.api.models import (
    EN_DASH,
    CompletenessStat,
    FeedQualityFeed,
    FeedQualityResponse,
    FeedQualitySummary,
    QualityBreakdown,
    ScoreBucket,
)
from stewards.monitors import quality, transforms
from stewards.monitors.health import HealthState, Movement
from stewards.monitors.registry import Col, ColKind, get_monitor
from stewards.monitors.thresholds import Tone

MONITOR = get_monitor("feed_quality")


@pytest.fixture
def snapshot() -> FeedQualityResponse:
    return FeedQualityResponse.model_validate(load_sample("feed_quality_quality"))


@pytest.fixture
def rows(snapshot: FeedQualityResponse) -> tuple[quality.QualityRow, ...]:
    return quality.build_rows(snapshot.data)


def feed(**overrides: object) -> FeedQualityFeed:
    """A feed with the fields a test cares about, and defaults for the rest."""
    payload: dict[str, object] = {
        "feed_id": "pub-feed",
        "feed_url": "https://example.test/api/feeds/slots",
        "feed_type": "Slot",
        "feed_version": "V2.0",
        "is_regular": True,
        "dataset_url": "https://example.test/OpenActive",
        "dataset_name": "Example Sessions",
        "publisher_id": "pub_example",
        "publisher_name": "Example",
        "status": "OK",
        "grade": "Gold",
        "score": 90.0,
        "num_future_opportunity_items": 10,
        "completeness": {"location": 100, "activities": 0},
        "warnings": [],
        "errors": [],
        "missing_required_fields": {},
        "last_assessed": "2026-08-21T01:56:24.156787Z",
    }
    payload.update(overrides)
    return FeedQualityFeed.model_validate(payload)


# --- rows ---------------------------------------------------------------------------------


def test_the_snapshot_becomes_one_row_per_feed(rows: tuple[quality.QualityRow, ...]) -> None:
    assert len(rows) == 15
    assert {row.publisher_name for row in rows}
    assert all(row.feed_name for row in rows)


def test_an_empty_snapshot_yields_no_rows_rather_than_raising() -> None:
    assert quality.build_rows(()) == ()
    frame = quality.to_dataframe(MONITOR, ())
    assert frame.empty
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert list(quality.tone_frame(MONITOR, ()).columns) == list(frame.columns)


def test_a_feed_is_named_by_the_last_segment_of_its_url() -> None:
    assert quality.feed_label(feed(feed_url="https://x.test/api/feeds/Leeds-live-slots")) == (
        "Leeds-live-slots"
    )
    assert quality.feed_label(feed(feed_url="https://x.test/api/feeds/slots/")) == "slots"


def test_a_feed_with_no_url_falls_back_to_its_id_then_to_unknown() -> None:
    assert quality.feed_label(feed(feed_url="", feed_id="only-an-id")) == "only-an-id"
    assert quality.feed_label(feed(feed_url="", feed_id="")) == quality.UNKNOWN


def test_a_dataset_with_no_name_is_identified_by_its_url() -> None:
    assert quality.dataset_label(feed(dataset_name="   ")) == "https://example.test/OpenActive"
    assert quality.dataset_label(feed(dataset_name="", dataset_url="")) == quality.UNKNOWN


def test_feeds_are_grouped_by_dataset_url_not_by_its_name() -> None:
    """Several publishers report the same generic dataset name, so the URL is the identity."""
    first = feed(dataset_url="https://a.test/OpenActive", dataset_name=" Sessions")
    second = feed(dataset_url="https://b.test/OpenActive", dataset_name=" Sessions")
    assert quality.dataset_key(first) != quality.dataset_key(second)


def test_regularity_reads_as_words_and_says_unknown_when_unreported() -> None:
    assert quality.regularity(feed(is_regular=True)) == "Regular"
    assert quality.regularity(feed(is_regular=False)) == "Irregular"
    assert quality.regularity(feed(is_regular=None)) == quality.UNKNOWN


def test_an_unreported_categorical_field_reads_as_unknown_so_it_can_be_filtered() -> None:
    row = quality.build_rows((feed(feed_type="", grade=None, feed_version=" "),))[0]
    assert row.feed_type == quality.UNKNOWN
    assert row.grade == quality.UNKNOWN
    assert row.feed_version == quality.UNKNOWN


# --- completeness -------------------------------------------------------------------------


def test_completeness_averages_only_the_fields_the_assessment_reported() -> None:
    assert quality.mean_completeness({"a": 100, "b": 0, "c": None}) == 50.0


def test_a_feed_that_reported_no_completeness_has_none_rather_than_zero() -> None:
    """Zero would read as "nothing is populated", which is a different claim."""
    assert quality.mean_completeness({}) is None
    assert quality.mean_completeness({"a": None}) is None


def test_the_completeness_table_keeps_a_field_the_assessment_did_not_reach() -> None:
    row = quality.build_rows((feed(completeness={"location": 100, "level": None}),))[0]
    frame = quality.completeness_frame(row)
    assert list(frame["Field"]) == ["Level", "Location"]  # unreported first, then worst-up
    assert frame["Coverage"].isna().iloc[0]


def test_the_completeness_table_of_a_feed_with_no_figures_is_empty_not_an_error() -> None:
    row = quality.build_rows((feed(completeness={}),))[0]
    assert quality.completeness_frame(row).empty


# --- dataset scores and ordering ----------------------------------------------------------


def test_a_dataset_score_is_the_mean_of_the_feeds_that_were_scored() -> None:
    feeds = (
        feed(feed_id="a", score=100.0),
        feed(feed_id="b", score=50.0),
        feed(feed_id="c", score=None),
    )
    assert quality.dataset_scores(feeds) == {"https://example.test/OpenActive": 75.0}


def test_a_dataset_none_of_whose_feeds_were_scored_has_no_score() -> None:
    feeds = (feed(feed_id="a", score=None),)
    assert quality.dataset_scores(feeds) == {"https://example.test/OpenActive": None}


def test_a_datasets_feeds_come_out_consecutive(rows: tuple[quality.QualityRow, ...]) -> None:
    keys = [row.dataset_key for row in quality.sort_rows(MONITOR, rows)]
    assert len(list(dict.fromkeys(keys))) == len(set(keys))


def test_datasets_are_ordered_best_first_and_feeds_within_them_too() -> None:
    feeds = (
        feed(feed_id="low", dataset_url="https://low.test", dataset_name="Low", score=40.0),
        feed(feed_id="hi1", dataset_url="https://hi.test", dataset_name="High", score=70.0),
        feed(feed_id="hi2", dataset_url="https://hi.test", dataset_name="High", score=95.0),
    )
    ordered = quality.sort_rows(MONITOR, quality.build_rows(feeds))
    assert [row.feed_id for row in ordered] == ["hi2", "hi1", "low"]


def test_an_unscored_feed_sorts_last_and_a_zero_scored_one_does_not() -> None:
    """Zero is a real score and belongs above a feed the batch could not score at all."""
    feeds = (
        feed(feed_id="none", score=None),
        feed(feed_id="zero", score=0.0),
    )
    ordered = quality.sort_rows(MONITOR, quality.build_rows(feeds))
    assert [row.feed_id for row in ordered] == ["zero", "none"]


def test_two_datasets_scoring_alike_do_not_interleave_their_feeds() -> None:
    feeds = (
        feed(feed_id="a1", dataset_url="https://a.test", dataset_name="A", score=80.0),
        feed(feed_id="b1", dataset_url="https://b.test", dataset_name="B", score=80.0),
        feed(feed_id="a2", dataset_url="https://a.test", dataset_name="A", score=80.0),
    )
    ordered = quality.sort_rows(MONITOR, quality.build_rows(feeds))
    assert [row.dataset_key for row in ordered].count("https://a.test") == 2
    assert [row.feed_id for row in ordered] == ["a1", "a2", "b1"]


def test_sorting_an_empty_snapshot_is_an_empty_list() -> None:
    assert quality.sort_rows(MONITOR, ()) == []


# --- the table ----------------------------------------------------------------------------


def test_the_frame_carries_the_declared_columns_in_order(
    rows: tuple[quality.QualityRow, ...],
) -> None:
    frame = quality.to_dataframe(MONITOR, quality.sort_rows(MONITOR, rows))
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert len(frame) == len(rows)


def test_an_unscored_feed_renders_a_blank_score_not_a_zero() -> None:
    frame = quality.to_dataframe(MONITOR, quality.build_rows((feed(score=None),)))
    assert frame["Score"].isna().all()


def test_the_issue_count_is_errors_plus_warnings() -> None:
    row = quality.build_rows((feed(errors=["broken"], warnings=["thin", "stale"]),))[0]
    assert (row.error_count, row.warning_count, row.issue_count) == (1, 2, 3)
    assert quality.to_dataframe(MONITOR, (row,))["Issues"].iloc[0] == 3


@pytest.mark.parametrize(
    ("score", "tone"),
    [(100.0, Tone.GREEN), (80.0, Tone.GREEN), (79.9, Tone.AMBER), (60.0, Tone.AMBER)],
)
def test_the_score_column_shades_on_the_quality_scale(score: float, tone: Tone) -> None:
    """80 and 60 are the boundaries, and they are inclusive."""
    col = MONITOR.column("Score")
    assert quality.cell_tone(col, score) is tone


def test_an_unreported_score_carries_the_grey_tone_rather_than_red() -> None:
    assert quality.cell_tone(MONITOR.column("Score"), None) is Tone.GREY


def test_the_status_column_shades_the_assessments_own_tokens() -> None:
    col = MONITOR.column("Status")
    assert quality.cell_tone(col, "OK") is Tone.GREEN
    assert quality.cell_tone(col, "WARNING") is Tone.AMBER
    assert quality.cell_tone(col, "ERROR") is Tone.RED
    assert quality.cell_tone(col, None) is Tone.GREY


def test_a_column_with_no_rag_meaning_is_left_unshaded() -> None:
    assert quality.cell_tone(MONITOR.column("Publisher"), "Anything") is None
    assert quality.cell_tone(MONITOR.column("Future items"), 10) is None


def test_a_risk_column_shades_the_other_way_up() -> None:
    """No quality column declares one today; the mapping exists so one can, as it does on an
    incident table."""
    col = Col("share", "Share", ColKind.RISK)
    assert quality.cell_tone(col, 60.0) is Tone.RED
    assert quality.cell_tone(col, 0.0) is Tone.GREEN


def test_the_tone_frame_lines_up_with_the_table(
    rows: tuple[quality.QualityRow, ...],
) -> None:
    shown = quality.sort_rows(MONITOR, rows)
    frame = quality.to_dataframe(MONITOR, shown)
    tones = quality.tone_frame(MONITOR, shown)
    assert tones.shape == frame.shape
    assert set(tones["Publisher"]) == {""}
    assert transforms.rag_columns(MONITOR) == ["Status", "Score", "Completeness"]


# --- filtering ----------------------------------------------------------------------------


def test_filter_options_are_the_distinct_values_in_the_snapshot(
    rows: tuple[quality.QualityRow, ...],
) -> None:
    assert quality.filter_options(rows, "status") == ["ERROR", "OK", "WARNING"]
    assert quality.filter_options((), "status") == []


def test_search_matches_publisher_dataset_feed_and_id(
    rows: tuple[quality.QualityRow, ...],
) -> None:
    assert len(quality.search_rows(rows, "")) == len(rows)
    assert quality.search_rows(rows, "no-such-publisher") == []
    assert quality.search_rows(rows, "ACTIVE LUTON")


def test_a_selection_narrows_to_that_value(rows: tuple[quality.QualityRow, ...]) -> None:
    shown = quality.apply_filters(rows, selections={"status": "ERROR"})
    assert shown
    assert {row.status for row in shown} == {"ERROR"}


def test_an_empty_selection_is_ignored(rows: tuple[quality.QualityRow, ...]) -> None:
    assert len(quality.apply_filters(rows, selections={"status": ""})) == len(rows)


def test_the_issues_toggle_keeps_only_flagged_feeds(
    rows: tuple[quality.QualityRow, ...],
) -> None:
    shown = quality.apply_filters(rows, issues_only=True)
    assert shown
    assert all(row.issue_count for row in shown)
    assert len(shown) < len(rows)


def test_filters_over_an_empty_snapshot_stay_empty() -> None:
    assert quality.apply_filters((), search="x", issues_only=True) == []


# --- the page figures ---------------------------------------------------------------------


def summary(**overrides: object) -> FeedQualitySummary:
    payload: dict[str, object] = {
        "total_feeds": 10,
        "feeds_scored": 8,
        "feeds_ok": 6,
        "feeds_with_warnings": 2,
        "feeds_with_errors": 2,
        "datasets_with_errors": 1,
        "feeds_with_future_data": 7,
        "total_future_opportunity_items": 1234,
        "average_score": 85.0,
        "median_score": 90.0,
    }
    payload.update(overrides)
    return FeedQualitySummary.model_validate(payload)


def test_the_page_states_five_figures_off_the_summary_block() -> None:
    stats = quality.page_stats(summary())
    assert len(stats) == 5
    assert [stat.value for stat in stats] == ["85.0", "8", "2", "2", "1,234"]
    assert stats[0].tone is Tone.GREEN
    assert stats[1].tone is None  # a count that is context, not a state


def test_a_figure_the_batch_did_not_report_reads_em_dash_with_no_tone() -> None:
    """Null is not zero: an unreported figure must not render as an all-clear."""
    stats = quality.page_stats(FeedQualitySummary())
    assert {stat.value for stat in stats} == {transforms.EMPTY}
    assert all(stat.tone is None for stat in stats)


def test_a_reported_zero_is_grey_rather_than_alarming() -> None:
    stats = quality.page_stats(summary(feeds_with_errors=0))
    assert stats[2].value == "0"
    assert stats[2].tone is Tone.GREY


# --- the card -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("average", "state"),
    [
        (100.0, HealthState.HEALTHY),
        (80.0, HealthState.HEALTHY),
        (79.9, HealthState.WARNING),
        (60.0, HealthState.WARNING),
        (59.9, HealthState.CRITICAL),
    ],
)
def test_the_card_state_follows_the_quality_bands(average: float, state: HealthState) -> None:
    """The same boundaries the Score column shades on, so the two cannot disagree."""
    assert quality.assess_quality(summary(average_score=average)).state is state


def test_the_card_claims_no_movement_it_cannot_support() -> None:
    """One snapshot carries a level and says nothing about direction."""
    health = quality.assess_quality(summary())
    assert health.movement is Movement.UNKNOWN
    assert health.points == 0
    assert health.current == 85.0


def test_a_snapshot_with_no_average_is_unknown_rather_than_healthy() -> None:
    health = quality.assess_quality(FeedQualitySummary())
    assert health.state is HealthState.UNKNOWN
    assert health.reason == quality.NOT_SCORED


def test_the_card_carries_the_headline_and_four_supporting_figures() -> None:
    card = quality.tile_card(summary())
    assert card.value == "85.0"
    assert card.unit == "average quality score"
    assert [fact.label for fact in card.facts] == [
        "Feeds OK",
        "Warnings",
        "Errors",
        "Datasets with errors",
    ]
    assert [fact.value for fact in card.facts] == ["6", "2", "2", "1"]
    assert card.badge == 2
    assert "8 of 10 feeds scored" in card.note


def test_an_unreported_card_figure_reads_em_dash_and_claims_nothing() -> None:
    card = quality.tile_card(FeedQualitySummary())
    assert card.value == transforms.EMPTY
    assert {fact.value for fact in card.facts} == {transforms.EMPTY}
    assert all(fact.tone is None for fact in card.facts)
    assert card.badge is None
    assert card.note == quality.NOT_SCORED


def test_a_fleet_with_no_errors_gets_no_sidebar_pill() -> None:
    assert quality.tile_card(summary(feeds_with_errors=0)).badge == 0


# --- the selected row ---------------------------------------------------------------------


def test_the_assessment_panel_lists_errors_then_warnings() -> None:
    row = quality.build_rows((feed(errors=["parse failed"], warnings=["no future items"]),))[0]
    assert quality.issue_lines(row) == (
        ("Error", "parse failed"),
        ("Warning", "no future items"),
    )


def test_a_clean_feed_has_no_issue_lines() -> None:
    assert quality.issue_lines(quality.build_rows((feed(),))[0]) == ()


def test_missing_required_fields_read_as_one_line_per_opportunity_type() -> None:
    row = quality.build_rows(
        (feed(missing_required_fields={"Slot": ["activity", "location"], "Event": []}),)
    )[0]
    assert quality.missing_field_lines(row) == ("Event", "Slot: activity, location")


def test_a_field_name_is_de_slugged_for_display() -> None:
    assert quality.field_label("accessibility_support") == "Accessibility support"
    assert quality.field_label("") == quality.UNKNOWN


def test_an_unassessed_feed_reports_no_assessment_date() -> None:
    row = quality.build_rows((feed(last_assessed=None),))[0]
    assert row.last_assessed == transforms.EMPTY
    assert quality.build_rows((feed(),))[0].last_assessed == "2026-08-21"


# --- the charts ---------------------------------------------------------------------------

PALETTE = {
    Tone.RED: "#C6413B",
    Tone.AMBER: "#C77F1A",
    Tone.GREEN: "#1F7A4C",
    Tone.GREY: "#5C6B76",
}
COLOURS = {"label_colour": "#5C6B76", "grid_colour": "#EDF0F2"}


def test_every_chart_draws_from_the_sample_snapshot(snapshot: FeedQualityResponse) -> None:
    block = snapshot.summary
    assert quality.score_distribution_chart(block, PALETTE, **COLOURS) is not None
    assert quality.completeness_chart(block, PALETTE, **COLOURS) is not None
    assert quality.status_chart(block, PALETTE, **COLOURS) is not None
    assert quality.grade_chart(block, "#0E8F8A", **COLOURS) is not None


def test_a_chart_with_nothing_to_draw_returns_none_rather_than_an_empty_axis() -> None:
    empty = FeedQualitySummary()
    assert quality.score_distribution_chart(empty, PALETTE, **COLOURS) is None
    assert quality.completeness_chart(empty, PALETTE, **COLOURS) is None
    assert quality.status_chart(empty, PALETTE, **COLOURS) is None
    assert quality.grade_chart(empty, "#0E8F8A", **COLOURS) is None


def test_the_score_bands_are_shaded_on_the_quality_scale() -> None:
    block = FeedQualitySummary(
        score_buckets=(
            ScoreBucket(lower=0, upper=20, feed_count=1),
            ScoreBucket(lower=60, upper=80, feed_count=2),
            ScoreBucket(lower=80, upper=100, feed_count=3),
        )
    )
    chart = quality.score_distribution_chart(block, PALETTE, **COLOURS)
    assert chart is not None
    assert list(chart.data["colour"]) == [
        PALETTE[Tone.RED],
        PALETTE[Tone.AMBER],
        PALETTE[Tone.GREEN],
    ]
    assert list(chart.data["label"]) == [
        f"0{EN_DASH}20",
        f"60{EN_DASH}80",
        f"80{EN_DASH}100",
    ]


def test_completeness_is_drawn_best_first_and_skips_a_field_with_no_average() -> None:
    block = FeedQualitySummary(
        completeness={
            "level": CompletenessStat(average=5.0, feeds_reporting=3),
            "location": CompletenessStat(average=95.0, feeds_reporting=3),
            "activities": CompletenessStat(average=None, feeds_reporting=0),
        }
    )
    chart = quality.completeness_chart(block, PALETTE, **COLOURS)
    assert chart is not None
    assert list(chart.data["label"]) == ["Location", "Level"]


def test_the_status_chart_labels_and_shades_the_assessments_tokens() -> None:
    block = FeedQualitySummary(
        status_breakdown=(
            QualityBreakdown(value="OK", feed_count=6),
            QualityBreakdown(value="ERROR", feed_count=2),
        )
    )
    chart = quality.status_chart(block, PALETTE, **COLOURS)
    assert chart is not None
    assert list(chart.data["label"]) == ["OK", "Error"]
    assert list(chart.data["colour"]) == [PALETTE[Tone.GREEN], PALETTE[Tone.RED]]


def test_a_grade_the_assessment_could_not_award_is_counted_not_dropped() -> None:
    block = FeedQualitySummary(
        grade_breakdown=(
            QualityBreakdown(value="unknown", feed_count=4),
            QualityBreakdown(value="Gold", feed_count=1),
        )
    )
    chart = quality.grade_chart(block, "#0E8F8A", **COLOURS)
    assert chart is not None
    assert list(chart.data["label"]) == ["Unknown", "Gold"]
    assert set(chart.data["colour"]) == {"#0E8F8A"}


# --- the payload itself -------------------------------------------------------------------


def test_the_response_tolerates_a_null_where_a_collection_is_expected() -> None:
    """A batch that sends null for a list it did not compute must not take the page down."""
    row = FeedQualityFeed.model_validate(
        {"feed_id": "x", "errors": None, "completeness": None, "grade": None}
    )
    assert row.errors == ()
    assert row.completeness == {}
    assert row.grade is None


def test_a_score_band_labels_itself() -> None:
    assert ScoreBucket(lower=20, upper=40, feed_count=0).label == f"20{EN_DASH}40"


def test_the_assessment_timestamp_is_parsed(snapshot: FeedQualityResponse) -> None:
    assert isinstance(snapshot.summary.newest_assessment, datetime)
