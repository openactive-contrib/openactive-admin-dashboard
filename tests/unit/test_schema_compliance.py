"""Joining the quality and custom-property snapshots into one row per feed."""

from __future__ import annotations

from typing import Any

import pandas as pd

from fixture_loader import load_sample
from stewards.api.models import (
    CustomPropertyResponse,
    FeedQualityResponse,
    SchemaComplianceSnapshot,
)
from stewards.monitors.health import HealthState, Movement
from stewards.monitors.quality import Stat
from stewards.monitors.registry import Col, ColKind
from stewards.monitors.schema_compliance import (
    FIELD_WARNING_BELOW,
    KEY_FIELDS,
    NOT_ASSESSED,
    VOCABULARY_COLUMNS,
    VOCABULARY_WARNING_BELOW,
    ComplianceRow,
    Verdict,
    apply_filters,
    assess_compliance,
    build_rows,
    key_field_averages,
    page_stats,
    resolve,
    sort_rows,
    tile_card,
    tile_note,
    to_dataframe,
    tone_frame,
    vocabulary_frame,
)
from stewards.monitors.thresholds import Tone, status_label, status_tone
from stewards.monitors.transforms import EMPTY

SHARED_FEED = "api-letsride-co-uk-public-v1-rides"
META = load_sample("feed_quality_quality")["meta"]

#: Every key field fully covered: a feed with this and a version passes.
FULL = dict.fromkeys(KEY_FIELDS, 100)


def _snapshot(*, with_properties: bool = True) -> SchemaComplianceSnapshot:
    return SchemaComplianceSnapshot(
        quality=FeedQualityResponse.model_validate(load_sample("feed_quality_quality")),
        custom_properties=(
            CustomPropertyResponse.model_validate(
                load_sample("feed_custom_properties_properties")
            )
            if with_properties
            else None
        ),
    )


def _one(quality_feed: dict[str, Any], property_feeds: list[dict[str, Any]]) -> ComplianceRow:
    snapshot = SchemaComplianceSnapshot(
        quality=FeedQualityResponse.model_validate({"data": [quality_feed], "meta": META}),
        custom_properties=CustomPropertyResponse.model_validate(
            {"data": property_feeds, "meta": META}
        ),
    )
    (row,) = build_rows(snapshot)
    return row


def _row(rows: tuple[ComplianceRow, ...], feed_id: str) -> ComplianceRow:
    return next(row for row in rows if row.feed_id == feed_id)


def test_there_is_one_row_per_feed_in_the_quality_snapshot() -> None:
    rows = build_rows(_snapshot())
    assert len(rows) == 15
    assert len({row.feed_id for row in rows}) == 15


def test_a_feed_in_both_snapshots_carries_its_custom_properties() -> None:
    row = _row(build_rows(_snapshot()), SHARED_FEED)
    assert row.properties is not None
    assert row.properties.feed_id == SHARED_FEED
    assert row.custom_property_count
    assert row.custom_property_count > 0


def test_a_feed_missing_from_the_custom_property_list_uses_none() -> None:
    row = _one({"feed_id": "clean"}, [{"feed_id": "other", "num_custom_properties": 4}])
    assert row.properties is None
    assert row.custom_property_count == 0
    assert row.outside_beta_count == 0


def test_a_failed_custom_property_read_is_unknown_not_zero() -> None:
    rows = build_rows(_snapshot(with_properties=False))
    assert len(rows) == 15
    assert all(row.custom_property_count is None for row in rows)
    assert all(row.outside_beta_count is None for row in rows)


def test_a_feed_only_in_the_custom_property_snapshot_is_left_out() -> None:
    row = _one({"feed_id": "assessed"}, [{"feed_id": "not-assessed"}])
    assert row.feed_id == "assessed"


def test_key_completeness_lists_every_key_field_in_order() -> None:
    row = _one(
        {"feed_id": "f", "completeness": {"location": 80, "level": None, "extra_optional": 5}},
        [],
    )
    assert tuple(row.key_completeness) == KEY_FIELDS
    assert row.key_completeness["location"] == 80
    assert row.key_completeness["level"] is None
    assert row.key_completeness["facilities"] is None
    assert "extra_optional" not in row.key_completeness
    assert row.mean_key_completeness == 80


def test_missing_required_fields_are_counted_once_across_opportunity_types() -> None:
    row = _one(
        {
            "feed_id": "f",
            "missing_required_fields": {
                "SessionSeries": ["name", "location"],
                "ScheduledSession": ["location", "startDate"],
            },
        },
        [],
    )
    assert row.missing_required_count == 3
    # And named once each, for the same reason: three distinct fields, three names.
    assert row.missing_required_label == "location, name, startDate"


def test_a_feed_missing_nothing_names_nothing() -> None:
    assert _one({"feed_id": "f"}, []).missing_required_label == EMPTY


def test_the_properties_cell_names_every_property_the_feed_publishes() -> None:
    row = _one(
        {"feed_id": "f"},
        [
            {
                "feed_id": "f",
                "custom_properties": [
                    {"property": "beta:video", "entity_type": "Event"},
                    {"property": "alternateName", "entity_type": "Place"},
                    {"property": "beta:video", "entity_type": "Place"},
                ],
            }
        ],
    )
    # Once each, alphabetically, however many entity types carry them.
    assert row.custom_property_label == "alternateName, beta:video"
    assert row.custom_property_count == 2


def test_a_feed_with_no_properties_and_a_failed_read_both_name_nothing() -> None:
    """The cell cannot tell them apart, and does not try: the caption above the table and
    the count beside it already do."""
    assert _one({"feed_id": "f"}, []).custom_property_label == EMPTY
    (failed,) = _rows(_feed(), with_properties=False)
    assert failed.custom_property_label == EMPTY
    assert failed.custom_property_count is None


def test_a_feed_with_no_version_reads_unknown() -> None:
    row = _one({"feed_id": "f", "feed_version": ""}, [])
    assert row.feed_version == "Unknown"


# --- the verdict ---------------------------------------------------------------------------


def _feed(feed_id: str = "f", **fields: Any) -> dict[str, Any]:
    """A feed that passes unless the test overrides something."""
    return {"feed_id": feed_id, "feed_version": "V2.0", "completeness": FULL, **fields}


def _rows(*feeds: dict[str, Any], with_properties: bool = True) -> tuple[ComplianceRow, ...]:
    return build_rows(
        SchemaComplianceSnapshot(
            quality=FeedQualityResponse.model_validate({"data": list(feeds), "meta": META}),
            custom_properties=(
                CustomPropertyResponse.model_validate({"data": [], "meta": META})
                if with_properties
                else None
            ),
        )
    )


def test_a_complete_feed_with_a_version_passes() -> None:
    (row,) = _rows(_feed())
    assert row.verdict is Verdict.PASS
    assert row.reasons == ()


def test_a_missing_required_field_fails_whatever_else_is_true() -> None:
    (row,) = _rows(_feed(missing_required_fields={"SessionSeries": ["name"]}))
    assert row.verdict is Verdict.FAIL
    assert row.reasons[0] == "1 required field missing"


def test_a_poorly_covered_key_field_warns_and_names_the_field() -> None:
    (row,) = _rows(_feed(completeness={**FULL, "start_date": 59.9, "level": 60}))
    assert row.verdict is Verdict.WARNING
    assert row.weak_fields == ("start_date",)
    assert row.reasons == ("Below 60% complete: start date",)


def test_an_unreported_field_is_not_a_poorly_covered_one() -> None:
    (row,) = _rows(_feed(completeness={**FULL, "level": None}))
    assert row.verdict is Verdict.PASS


def test_no_completeness_at_all_warns() -> None:
    (row,) = _rows(_feed(completeness={}))
    assert row.verdict is Verdict.WARNING
    assert "Completeness of the key fields was not reported" in row.reasons


def test_an_unknown_version_warns() -> None:
    (row,) = _rows(_feed(feed_version=""))
    assert row.verdict is Verdict.WARNING
    assert row.reasons == ("Feed version not reported",)


def test_the_verdicts_read_as_toned_status_labels() -> None:
    assert (status_label("pass"), status_tone("pass")) == ("Pass", Tone.GREEN)
    assert (status_label("warning"), status_tone("warning")) == ("Warning", Tone.AMBER)
    assert (status_label("fail"), status_tone("fail")) == ("Fail", Tone.RED)


# --- sorting and filtering -----------------------------------------------------------------


def test_rows_sort_worst_first() -> None:
    rows = _rows(
        _feed("pass"),
        _feed("warn", completeness={**FULL, "location": 10}),
        _feed("fail-one", missing_required_fields={"A": ["name"]}),
        _feed("fail-two", missing_required_fields={"A": ["name", "url"]}),
    )
    assert [row.feed_id for row in sort_rows(rows)] == ["fail-two", "fail-one", "warn", "pass"]


def test_completeness_columns_read_one_key_field() -> None:
    (row,) = _rows(_feed(completeness={**FULL, "location": 42}))
    assert resolve(row, "completeness.location") == 42
    assert resolve(row, "feed_version") == "V2.0"


def test_not_passing_only_hides_the_passes() -> None:
    rows = _rows(_feed("pass"), _feed("fail", missing_required_fields={"A": ["name"]}))
    assert [row.feed_id for row in apply_filters(rows, not_passing_only=True)] == ["fail"]


def test_a_status_selection_matches_the_verdict_token() -> None:
    rows = _rows(_feed("pass"), _feed("fail", missing_required_fields={"A": ["name"]}))
    picked = apply_filters(rows, selections={"status": "fail"})
    assert [row.feed_id for row in picked] == ["fail"]


def test_search_matches_the_feed_id() -> None:
    rows = _rows(_feed("alpha"), _feed("beta"))
    assert [row.feed_id for row in apply_filters(rows, search="ALP")] == ["alpha"]


# --- the table -----------------------------------------------------------------------------

COLUMNS = (
    Col("feed_id", "Feed", ColKind.MONO),
    Col("status", "Status", ColKind.STATUS),
    Col("completeness.location", "Location", ColKind.PERCENT),
)


def test_the_table_labels_the_verdict_and_shades_the_cells() -> None:
    rows = _rows(_feed(missing_required_fields={"A": ["name"]}, completeness={"location": 70}))
    frame = to_dataframe(COLUMNS, rows)
    tones = tone_frame(COLUMNS, rows)
    assert frame.iloc[0].tolist() == ["f", "Fail", 70.0]
    assert tones.iloc[0].tolist() == ["", "red", "amber"]


def test_an_empty_table_keeps_its_columns() -> None:
    assert list(to_dataframe(COLUMNS, ()).columns) == ["Feed", "Status", "Location"]


# --- the figures ---------------------------------------------------------------------------


def test_page_stats_count_each_verdict() -> None:
    rows = _rows(
        _feed("pass"),
        _feed("warn", completeness={**FULL, "location": 10}),
        _feed("fail", missing_required_fields={"A": ["name"]}),
    )
    stats = {stat.label: stat for stat in page_stats(rows)}
    assert stats["Feeds checked"].value == "3"
    assert (stats["Failing"].value, stats["Failing"].tone) == ("1", Tone.RED)
    assert (stats["Warnings"].value, stats["Warnings"].tone) == ("1", Tone.AMBER)
    assert stats["Non-standard properties"].value == "0"


def test_page_stats_say_when_custom_properties_were_not_reported() -> None:
    stats = {stat.label: stat for stat in page_stats(_rows(_feed(), with_properties=False))}
    assert stats["Non-standard properties"].value == "—"
    assert stats["Non-standard properties"].sub == "custom properties not reported"


def test_page_stats_of_no_rows_do_not_divide_by_zero() -> None:
    stats = {stat.label: stat for stat in page_stats(())}
    assert stats["Feeds checked"].value == "0"
    assert stats["Key field completeness"].value == "—"


def test_key_field_averages_skip_unreported_values() -> None:
    rows = _rows(
        _feed("a", completeness={"location": 100, "level": None}),
        _feed("b", completeness={"location": 50}),
    )
    averages = key_field_averages(rows)
    assert tuple(averages) == KEY_FIELDS
    assert averages["location"] == 75
    assert averages["level"] is None


# --- the registry entry --------------------------------------------------------------------


def test_every_declared_column_resolves_on_the_sample_rows() -> None:
    from stewards.monitors.registry import SCHEMA_COMPLIANCE

    rows = build_rows(_snapshot())
    for col in SCHEMA_COMPLIANCE.columns:
        assert any(resolve(row, col.field) is not None for row in rows), col.field


def test_every_completeness_column_is_a_key_field() -> None:
    from stewards.monitors.registry import SCHEMA_COMPLIANCE

    fields = [c.field for c in SCHEMA_COMPLIANCE.columns if c.field.startswith("completeness.")]
    assert [f.removeprefix("completeness.") for f in fields] == list(KEY_FIELDS)


def test_every_declared_filter_has_options_on_the_sample_rows() -> None:
    from stewards.monitors.registry import SCHEMA_COMPLIANCE
    from stewards.monitors.schema_compliance import filter_options

    rows = build_rows(_snapshot())
    for spec in SCHEMA_COMPLIANCE.filters:
        assert filter_options(rows, spec.field), spec.field


def test_the_table_builds_from_the_registry_columns() -> None:
    from stewards.monitors.registry import SCHEMA_COMPLIANCE

    rows = build_rows(_snapshot())
    frame = to_dataframe(SCHEMA_COMPLIANCE.columns, rows)
    assert len(frame) == 15
    assert list(frame.columns) == [c.label for c in SCHEMA_COMPLIANCE.columns]
    assert tone_frame(SCHEMA_COMPLIANCE.columns, rows).shape == frame.shape


# --- vocabulary adherence -------------------------------------------------------------------
#
# Two figures the nightly assessment added to the quality read. Sparse on purpose: it works a
# rolling five-day window, so most feeds carry nothing and the absence must not read as zero.

#: The payload shape the assessment sends, as one feed's activity block.
ADHERENCE = {
    "adherence_pct": 66.67,
    "invalid_id": 0,
    "items": 12,
    "label_match_only": 0,
    "other_scheme": 0,
    "top_unmatched": [],
    "unrecognised": 4,
    "valid_id": 8,
}

ACTIVITY_FEED = "activeluton-openactive-legendonlineservices-co-uk-api-sessions"
FACILITY_FEED = "better-admin-org-uk-api-openactive-better-facility-uses"


def test_a_feed_carries_the_vocabulary_figures_the_assessment_reported() -> None:
    row = _row(build_rows(_snapshot()), ACTIVITY_FEED)
    assert row.activity_adherence == 66.67
    assert row.vocabularies_reported
    assert row.activity_vocabulary is not None
    assert row.activity_vocabulary.items == 12
    assert row.activity_vocabulary.valid_id == 8
    assert row.activity_vocabulary.unrecognised == 4


def test_the_two_vocabularies_are_read_independently() -> None:
    """A session feed reports activities and a facility feed reports facilities; neither
    absence is a zero for the other."""
    activity = _row(build_rows(_snapshot()), ACTIVITY_FEED)
    facility = _row(build_rows(_snapshot()), FACILITY_FEED)
    assert activity.activity_adherence == 66.67
    assert activity.facility_adherence is None
    assert facility.facility_adherence == 91.3
    assert facility.activity_adherence is None


def test_a_feed_the_window_did_not_reach_reports_nothing_rather_than_zero() -> None:
    """The assessment covers five days, so most feeds carry no figure at all. Reading that
    as zero adherence would fail the whole fleet while the window fills in."""
    (row,) = _rows(_feed())
    assert row.activity_adherence is None
    assert row.facility_adherence is None
    assert row.activity_vocabulary is None
    assert not row.vocabularies_reported


def test_nothing_matching_is_an_adherence_of_zero_not_a_missing_figure() -> None:
    """The boundary that matters: 0.0 is falsy, and a feed where nothing matched has still
    been assessed."""
    nothing = {**ADHERENCE, "adherence_pct": 0.0, "valid_id": 0, "unrecognised": 12}
    (row,) = _rows(_feed(activity_vocabulary_adherence=nothing))
    assert row.activity_adherence == 0.0
    assert row.vocabularies_reported


def test_an_assessment_that_reported_no_counts_still_reads_as_assessed() -> None:
    (row,) = _rows(_feed(activity_vocabulary_adherence={"adherence_pct": 100.0}))
    assert row.activity_adherence == 100.0
    assert row.activity_vocabulary is not None
    assert row.activity_vocabulary.items is None
    assert row.activity_vocabulary.unrecognised is None


def test_an_unsettled_top_unmatched_does_not_cost_the_read() -> None:
    """`top_unmatched` is not declared on the model, because its element shape is not
    settled. Whatever it carries must be ignored rather than raise."""
    objects = {**ADHERENCE, "top_unmatched": [{"term": "Zumba Gold", "count": 3}]}
    (row,) = _rows(_feed(activity_vocabulary_adherence=objects))
    assert row.activity_adherence == 66.67
    assert not hasattr(row.activity_vocabulary, "top_unmatched")


# --- the vocabulary warning rule ------------------------------------------------------------


def test_a_poorly_matched_vocabulary_warns_but_never_fails() -> None:
    """A fail means a required field is absent. An unrecognised activity term is bad for
    discovery but the data is there, so it must not be made to mean the same thing."""
    poor = {**ADHERENCE, "adherence_pct": 40.0}
    (row,) = _rows(_feed(activity_vocabulary_adherence=poor))
    assert row.verdict is Verdict.WARNING
    assert row.weak_vocabularies == ("activities",)
    assert "Below 90% vocabulary match: activities" in row.reasons


def test_both_vocabularies_are_named_when_both_are_thin() -> None:
    poor = {**ADHERENCE, "adherence_pct": 10.0}
    (row,) = _rows(
        _feed(activity_vocabulary_adherence=poor, facility_vocabulary_adherence=poor)
    )
    assert row.weak_vocabularies == ("activities", "facilities")
    assert "Below 90% vocabulary match: activities, facilities" in row.reasons


def test_the_bar_is_the_boundary_itself() -> None:
    """At the bar is covered; a hair under it is not."""
    at_bar = {**ADHERENCE, "adherence_pct": VOCABULARY_WARNING_BELOW}
    (passing,) = _rows(_feed(activity_vocabulary_adherence=at_bar))
    assert passing.weak_vocabularies == ()
    assert passing.verdict is Verdict.PASS

    under = {**ADHERENCE, "adherence_pct": VOCABULARY_WARNING_BELOW - 0.1}
    (warning,) = _rows(_feed(activity_vocabulary_adherence=under))
    assert warning.weak_vocabularies == ("activities",)
    assert warning.verdict is Verdict.WARNING


def test_the_vocabulary_bar_is_stricter_than_the_completeness_one() -> None:
    """The two thresholds are separate constants on purpose: a figure that would pass as
    field completeness still warns as a vocabulary match."""
    assert VOCABULARY_WARNING_BELOW > FIELD_WARNING_BELOW
    between = {**ADHERENCE, "adherence_pct": FIELD_WARNING_BELOW + 1}
    (row,) = _rows(_feed(activity_vocabulary_adherence=between))
    assert row.weak_vocabularies == ("activities",)


def test_a_vocabulary_the_window_did_not_reach_is_silent_not_a_warning() -> None:
    """The asymmetry with the key fields: an unreported completeness figure warns, an
    unreported vocabulary does not. Warning on silence would fail the fleet during the
    backfill and then quietly un-fail it."""
    (row,) = _rows(_feed())
    assert row.weak_vocabularies == ()
    assert row.verdict is Verdict.PASS
    assert not any("vocabulary" in reason for reason in row.reasons)


def test_nothing_matching_warns_rather_than_reading_as_unassessed() -> None:
    nothing = {**ADHERENCE, "adherence_pct": 0.0, "valid_id": 0, "unrecognised": 12}
    (row,) = _rows(_feed(activity_vocabulary_adherence=nothing))
    assert row.weak_vocabularies == ("activities",)
    assert row.verdict is Verdict.WARNING


def test_a_missing_required_field_still_outranks_a_thin_vocabulary() -> None:
    poor = {**ADHERENCE, "adherence_pct": 10.0}
    (row,) = _rows(
        _feed(
            activity_vocabulary_adherence=poor,
            missing_required_fields={"SessionSeries": ["name"]},
        )
    )
    assert row.verdict is Verdict.FAIL


# --- the vocabulary figure ------------------------------------------------------------------


def _stat(rows: tuple[ComplianceRow, ...], label: str) -> Stat:
    return next(stat for stat in page_stats(rows) if stat.label == label)


def test_the_figure_pools_both_vocabularies_across_the_feeds_that_reported() -> None:
    stat = _stat(build_rows(_snapshot()), "Vocabulary match")
    # 100.0, 66.67, 91.3 and 78.5 over four of the fifteen feeds.
    assert stat.value == "84%"
    assert stat.sub == "mean across 4 of 15 feeds"


def test_the_figure_is_toned_against_the_rule_not_the_score_bands() -> None:
    """85% is green to `score_tone` but every row carrying it warns, so the figure above the
    table has to be amber."""
    poor = {**ADHERENCE, "adherence_pct": 85.0}
    assert _stat(_rows(_feed(activity_vocabulary_adherence=poor)), "Vocabulary match").tone is (
        Tone.AMBER
    )
    good = {**ADHERENCE, "adherence_pct": 95.0}
    assert _stat(_rows(_feed(activity_vocabulary_adherence=good)), "Vocabulary match").tone is (
        Tone.GREEN
    )


def test_a_fleet_with_no_vocabulary_figures_reads_em_dash_not_zero() -> None:
    stat = _stat(_rows(_feed()), "Vocabulary match")
    assert stat.value == EMPTY
    assert stat.tone is None
    assert stat.sub == "not reported for any feed yet"


def test_the_warnings_figure_no_longer_claims_to_be_only_about_key_fields() -> None:
    assert _stat(_rows(_feed()), "Warnings").sub == "key fields or vocabularies poorly covered"


# --- the vocabulary columns and breakdown ---------------------------------------------------


def _col(label: str) -> Col:
    from stewards.monitors.registry import SCHEMA_COMPLIANCE

    return next(col for col in SCHEMA_COMPLIANCE.columns if col.label == label)


def test_both_vocabularies_have_a_column_shaded_on_their_own_bar() -> None:
    """A `PERCENT` column defaults to the score bands, which call 85% green. These columns
    warn at 90, so the cell and the row it sits in must agree."""
    for label in ("Activity vocabulary", "Facility vocabulary"):
        col = _col(label)
        assert col.kind is ColKind.PERCENT
        assert col.warn_below == VOCABULARY_WARNING_BELOW


def test_the_column_bar_and_the_rule_cannot_drift_apart() -> None:
    """`registry.py` cannot import the constant without a cycle, so it repeats the number.
    This is the guard that keeps the two level."""
    assert _col("Activity vocabulary").warn_below == VOCABULARY_WARNING_BELOW
    assert _col("Facility vocabulary").warn_below == VOCABULARY_WARNING_BELOW


def test_a_cell_is_shaded_against_the_bar_rather_than_the_score_bands() -> None:
    eighty_five = {**ADHERENCE, "adherence_pct": 85.0}
    rows = _rows(_feed(activity_vocabulary_adherence=eighty_five))
    frame = tone_frame([_col("Activity vocabulary")], rows)
    assert frame.iloc[0]["Activity vocabulary"] == Tone.AMBER.value


def test_an_unassessed_cell_carries_no_tone_at_all() -> None:
    frame = tone_frame([_col("Activity vocabulary")], _rows(_feed()))
    assert frame.iloc[0]["Activity vocabulary"] == Tone.GREY.value


def test_the_breakdown_has_a_line_per_vocabulary_reported() -> None:
    row = _row(build_rows(_snapshot()), ACTIVITY_FEED)
    frame = vocabulary_frame(row)
    assert list(frame.columns) == list(VOCABULARY_COLUMNS)
    assert list(frame["Vocabulary"]) == ["Activities"]
    assert frame.iloc[0]["Match"] == 66.67
    assert frame.iloc[0]["Items"] == 12
    assert frame.iloc[0]["Matched by id"] == 8
    assert frame.iloc[0]["Unrecognised"] == 4


def test_a_feed_reporting_both_gets_both_lines_in_order() -> None:
    (row,) = _rows(
        _feed(
            activity_vocabulary_adherence=ADHERENCE,
            facility_vocabulary_adherence={**ADHERENCE, "adherence_pct": 100.0},
        )
    )
    frame = vocabulary_frame(row)
    assert list(frame["Vocabulary"]) == ["Activities", "Facilities"]


def test_an_unassessed_feed_gets_no_lines_rather_than_a_line_of_zeros() -> None:
    frame = vocabulary_frame(_rows(_feed())[0])
    assert frame.empty
    assert list(frame.columns) == list(VOCABULARY_COLUMNS)


def test_a_count_the_assessment_omitted_stays_blank() -> None:
    (row,) = _rows(_feed(activity_vocabulary_adherence={"adherence_pct": 50.0}))
    frame = vocabulary_frame(row)
    assert frame.iloc[0]["Match"] == 50.0
    assert pd.isna(frame.iloc[0]["Items"])
    assert pd.isna(frame.iloc[0]["Unrecognised"])


# --- the overview card ---------------------------------------------------------------------


def test_the_card_states_the_failing_feeds_and_the_fleet_figures() -> None:
    card = tile_card(_snapshot())
    assert card.value == "1"
    assert card.unit == "feeds failing"
    assert card.badge == 1
    assert [fact.label for fact in card.facts] == ["Feeds checked", "Warnings", "Key fields"]
    assert [fact.value for fact in card.facts] == ["15", "14", "37%"]
    assert card.note == "of 15 feeds assessed · this snapshot only"


def test_the_card_unit_is_the_one_the_registry_declares() -> None:
    """The overview reads the registry's unit, so a card that disagrees reads as a bug."""
    from stewards.monitors.registry import SCHEMA_COMPLIANCE

    assert tile_card(_snapshot()).unit == SCHEMA_COMPLIANCE.unit


def test_a_breach_of_the_standard_is_reported_as_the_reason() -> None:
    health = assess_compliance(build_rows(_snapshot()))
    assert health.state is HealthState.WARNING
    assert health.reason == "1 of 15 feeds missing a required field"
    assert health.movement is Movement.UNKNOWN
    assert health.points == 0
    assert health.current == 1.0


def test_no_feeds_at_all_is_unknown_not_an_all_clear() -> None:
    health = assess_compliance(())
    assert health.state is HealthState.UNKNOWN
    assert health.reason == "no feeds assessed in this snapshot"


def test_a_fleet_that_fully_meets_the_standard_is_healthy() -> None:
    health = assess_compliance(_rows(_feed("a"), _feed("b")))
    assert health.state is HealthState.HEALTHY
    assert health.reason == "all 2 feeds meet the standard"


def test_a_clean_share_is_held_back_from_healthy_while_a_key_field_is_thin() -> None:
    """A recommended field is not a breach, so it does not fail the fleet — but a card that
    called this HEALTHY would claim an all-clear the table contradicts."""
    rows = _rows(_feed("a"), _feed("b", completeness={**FULL, "level": 10}))
    health = assess_compliance(rows)
    assert health.state is HealthState.WARNING
    assert health.reason == "1 of 2 feeds fall short on key fields or vocabularies"


def test_the_compliant_share_bands_the_card_at_the_amber_edge() -> None:
    """60% compliant is the floor of the amber band in `thresholds.score_tone`; one more
    breach drops the card through it."""
    gone = {"missing_required_fields": {"SessionSeries": ["name"]}}
    at_edge = _rows(_feed("a"), _feed("b"), _feed("c"), _feed("d", **gone), _feed("e", **gone))
    assert assess_compliance(at_edge).state is HealthState.WARNING

    below = _rows(
        _feed("a"), _feed("b"), _feed("c", **gone), _feed("d", **gone), _feed("e", **gone)
    )
    assert assess_compliance(below).state is HealthState.CRITICAL


def test_the_note_of_an_empty_fleet_says_so_rather_than_counting_nothing() -> None:
    assert tile_note(()) == NOT_ASSESSED
    assert tile_note(_rows(_feed("a"))) == "of 1 feeds assessed · this snapshot only"
