"""Joining the quality and custom-property snapshots into one row per feed."""

from __future__ import annotations

from typing import Any

from fixture_loader import load_sample
from stewards.api.models import (
    CustomPropertyResponse,
    FeedQualityResponse,
    SchemaComplianceSnapshot,
)
from stewards.monitors.schema_compliance import KEY_FIELDS, ComplianceRow, build_rows

SHARED_FEED = "api-letsride-co-uk-public-v1-rides"
META = load_sample("feed_quality_quality")["meta"]


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
