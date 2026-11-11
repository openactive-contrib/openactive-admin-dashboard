"""Joining the quality and custom-property snapshots into one row per feed."""

from __future__ import annotations

from typing import Any

from fixture_loader import load_sample
from stewards.api.models import (
    CustomPropertyResponse,
    FeedQualityResponse,
    SchemaComplianceSnapshot,
)
from stewards.monitors.registry import Col, ColKind
from stewards.monitors.schema_compliance import (
    KEY_FIELDS,
    ComplianceRow,
    Verdict,
    apply_filters,
    build_rows,
    key_field_averages,
    page_stats,
    resolve,
    sort_rows,
    to_dataframe,
    tone_frame,
)
from stewards.monitors.thresholds import Tone, status_label, status_tone

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
