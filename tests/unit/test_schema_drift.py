"""The custom-property shaping: rows, figures, the property table, the selected feed, charts.

`monitors.schema_drift` backed a monitor of its own until the schema drift page was retired
into the compliance page. What it shapes now is that page's Properties tab and the
custom-property half of its join, so the table, filter and card tests went with the monitor
and these are what remain.

Every pure function gets a happy path, an empty-input case and the boundary most likely to
be quietly wrong — here, a figure the batch reported as null, which is not a zero.
"""

from __future__ import annotations

import pandas as pd
import pytest

from fixture_loader import load_sample
from stewards.api.models import (
    CustomPropertyFeed,
    CustomPropertyResponse,
    CustomPropertySummary,
    PropertyUsage,
)
from stewards.monitors import schema_drift
from stewards.monitors.schema_drift import PropertyRow
from stewards.monitors.transforms import EMPTY

#: Pinned locally rather than imported from `theme`, so these assert behaviour and not the
#: brand's current hexes — `test_theme.py` owns those.
TEAL = "#0E8F8A"
COLOURS = {"label_colour": "#5C6B76", "grid_colour": "#EDF0F2"}


@pytest.fixture
def snapshot() -> CustomPropertyResponse:
    return CustomPropertyResponse.model_validate(
        load_sample("feed_custom_properties_properties")
    )


@pytest.fixture
def rows(snapshot: CustomPropertyResponse) -> tuple[PropertyRow, ...]:
    return schema_drift.build_rows(snapshot.data)


def feed(**overrides: object) -> CustomPropertyFeed:
    """A feed with the fields a test cares about, and defaults for the rest."""
    payload: dict[str, object] = {
        "feed_id": "example-test-slots",
        "feed_url": "https://example.test/openactive/slots",
        "feed_type": "Slot",
        "is_regular": True,
        "dataset_url": "https://example.test/",
        "dataset_name": "Example Sessions",
        "publisher_id": "pub_example",
        "publisher_name": "Example",
        "sampled_items": 500,
        "num_custom_properties": 2,
        "num_custom_property_usages": 3,
        "custom_properties": [
            {
                "property": "beta:video",
                "namespace": "beta",
                "entity_type": "Event",
                "presence_pct": 40,
            },
            {
                "property": "beta:video",
                "namespace": "beta",
                "entity_type": "Place",
                "presence_pct": 100,
            },
            {
                "property": "alternateName",
                "namespace": None,
                "entity_type": "Place",
                "presence_pct": 12.5,
            },
        ],
    }
    payload.update(overrides)
    return CustomPropertyFeed.model_validate(payload)


def row(**overrides: object) -> PropertyRow:
    return schema_drift.build_row(feed(**overrides))


# --- rows ---------------------------------------------------------------------------------


def test_the_snapshot_becomes_one_row_per_feed(rows: tuple[PropertyRow, ...]) -> None:
    assert len(rows) == 12
    assert all(r.feed_name for r in rows)
    assert all(r.publisher_name for r in rows)


def test_an_empty_snapshot_yields_no_rows() -> None:
    assert schema_drift.build_rows(()) == ()


def test_a_row_reads_the_feed_its_counts_and_its_namespaces() -> None:
    result = row()
    assert result.feed_name == "slots"
    assert result.property_count == 2
    assert result.usage_count == 3
    assert result.outside_beta_count == 1  # alternateName; beta:video is inside beta
    assert result.namespaces == ("beta", schema_drift.UNPREFIXED)
    assert result.namespace_label == "beta, Unprefixed"
    assert result.property_label == "alternateName, beta:video"
    assert result.property_names == ("alternateName", "beta:video")
    assert result.entity_types == ("Event", "Place")
    assert result.uses_outside_beta


def test_namespaces_order_beta_then_publisher_prefixes_then_unprefixed(
    rows: tuple[PropertyRow, ...],
) -> None:
    cycling = next(r for r in rows if r.publisher_name == "British Cycling")
    assert cycling.namespaces == ("beta", "britishcycling", schema_drift.UNPREFIXED)


def test_a_beta_only_feed_has_nothing_outside_beta() -> None:
    beta_only = row(
        custom_properties=[
            {"property": "beta:video", "namespace": "beta", "entity_type": "Event"}
        ],
        num_custom_properties=1,
        num_custom_property_usages=1,
    )
    assert beta_only.outside_beta_count == 0
    assert not beta_only.uses_outside_beta


def test_a_null_count_is_read_off_the_list_the_payload_sent_beside_it() -> None:
    result = row(num_custom_properties=None, num_custom_property_usages=None)
    assert result.property_count == 2
    assert result.usage_count == 3


def test_a_null_count_beside_no_list_stays_unreported_rather_than_zero() -> None:
    result = row(
        num_custom_properties=None, num_custom_property_usages=None, custom_properties=None
    )
    assert result.property_count is None
    assert result.usage_count is None
    assert result.namespace_label == EMPTY
    assert result.property_label == EMPTY


def test_the_api_count_wins_over_the_list_where_both_are_sent() -> None:
    """The batch owns the figure; the list is only a fallback for a null."""
    assert row(num_custom_properties=9).property_count == 9


def test_a_feed_with_no_url_names_itself_by_its_id_and_a_nameless_dataset_by_its_url() -> None:
    result = row(feed_url="", dataset_name=" ")
    assert result.feed_name == "example-test-slots"
    assert result.dataset_name == "https://example.test/"
    assert row(feed_url="", feed_id="").feed_name == schema_drift.UNKNOWN
    assert row(dataset_name="", dataset_url="").dataset_name == schema_drift.UNKNOWN


def test_an_unreported_feed_type_and_publisher_read_as_unknown() -> None:
    result = row(feed_type=" ", publisher_name="")
    assert result.feed_type == schema_drift.UNKNOWN
    assert result.publisher_name == schema_drift.UNKNOWN


# --- the figures and the card -------------------------------------------------------------


def test_the_page_figures_come_straight_off_the_summary(
    snapshot: CustomPropertyResponse,
) -> None:
    stats = {stat.label: stat for stat in schema_drift.page_stats(snapshot.summary)}
    assert stats["Datasets with custom properties"].value == "62"
    assert stats["Datasets with custom properties"].sub == "of 159 datasets assessed"
    assert stats["Feeds with custom properties"].value == "161"
    assert stats["Feeds with custom properties"].sub == "40.6% of 397 feeds assessed"
    assert stats["Distinct custom properties"].value == "57"
    assert stats["Distinct custom properties"].sub == "463 usages across 4 namespaces"
    assert stats["Publishers with custom properties"].value == "61"
    # Context, not a state: nothing here is coloured.
    assert all(stat.tone is None for stat in stats.values())


def test_an_unreported_summary_reads_em_dash_not_zero() -> None:
    stats = schema_drift.page_stats(CustomPropertySummary())
    assert all(stat.value == EMPTY for stat in stats)
    assert stats[1].sub == f"{EMPTY} of {EMPTY} feeds assessed"


# --- the selected feed --------------------------------------------------------------------


def test_the_selected_feed_lists_its_uses_most_present_first() -> None:
    frame = schema_drift.use_frame(row())
    assert list(frame.columns) == list(schema_drift.USE_COLUMNS)
    assert list(frame["Presence"]) == [100.0, 40.0, 12.5]
    assert list(frame["Namespace"]) == ["beta", "beta", schema_drift.UNPREFIXED]


def test_a_use_with_no_presence_is_kept_and_sorts_last() -> None:
    frame = schema_drift.use_frame(
        row(
            custom_properties=[
                {"property": "a", "entity_type": "Event"},
                {"property": "b", "entity_type": "Event", "presence_pct": 1},
            ]
        )
    )
    assert list(frame["Property"]) == ["b", "a"]


def test_a_feed_with_no_uses_yields_an_empty_frame() -> None:
    frame = schema_drift.use_frame(row(custom_properties=[]))
    assert frame.empty
    assert list(frame.columns) == list(schema_drift.USE_COLUMNS)


# --- the property table -------------------------------------------------------------------


def test_the_property_table_is_most_widely_used_first(
    snapshot: CustomPropertyResponse,
) -> None:
    ordered = schema_drift.sort_properties(snapshot.summary)
    assert len(ordered) == 57
    assert ordered[0].property == "beta:formattedDescription"
    frame = schema_drift.property_frame(ordered)
    assert list(frame.columns) == [col.label for col in schema_drift.PROPERTY_COLUMNS]
    assert frame.iloc[0]["Feeds"] == 68
    assert frame.iloc[0]["Namespace"] == "beta"
    assert "FacilityUse" in frame.iloc[0]["Entity types"]


def test_a_property_reporting_no_feed_count_sorts_last_and_reads_em_dash() -> None:
    summary = CustomPropertySummary(
        property_breakdown=(
            PropertyUsage(property="a", feed_count=None),
            PropertyUsage(property="b", feed_count=0, namespace=None),
        )
    )
    ordered = schema_drift.sort_properties(summary)
    assert [u.property for u in ordered] == ["b", "a"]
    frame = schema_drift.property_frame(ordered)
    assert frame.iloc[0]["Namespace"] == schema_drift.UNPREFIXED
    assert frame.iloc[0]["Entity types"] == EMPTY
    assert pd.isna(frame.iloc[1]["Feeds"])


def test_an_empty_property_breakdown_yields_an_empty_table() -> None:
    frame = schema_drift.property_frame(schema_drift.sort_properties(CustomPropertySummary()))
    assert frame.empty
    assert list(frame.columns) == [col.label for col in schema_drift.PROPERTY_COLUMNS]


def test_feeds_using_a_property_lists_each_entity_type_most_present_first(
    rows: tuple[PropertyRow, ...],
) -> None:
    frame = schema_drift.feeds_using(rows, "beta:formattedDescription")
    assert list(frame.columns) == list(schema_drift.FEEDS_USING_COLUMNS)
    assert not frame.empty
    presence = [p for p in frame["Presence"] if p is not None]
    assert presence == sorted(presence, reverse=True)


def test_feeds_using_a_property_no_feed_lists_is_empty(rows: tuple[PropertyRow, ...]) -> None:
    frame = schema_drift.feeds_using(rows, "no:such")
    assert frame.empty
    assert list(frame.columns) == list(schema_drift.FEEDS_USING_COLUMNS)


# --- the charts ---------------------------------------------------------------------------


def test_the_property_chart_draws_the_most_used_first_and_stops_at_the_limit(
    snapshot: CustomPropertyResponse,
) -> None:
    chart = schema_drift.property_chart(snapshot.summary, TEAL, **COLOURS)
    assert chart is not None
    values = list(chart.data["value"])
    assert len(values) == schema_drift.CHART_LIMIT
    assert values == sorted(values, reverse=True)
    assert next(iter(chart.data["label"])) == "beta:formattedDescription"


def test_a_chart_with_nothing_to_draw_returns_none_rather_than_an_empty_axis() -> None:
    assert schema_drift.property_chart(CustomPropertySummary(), TEAL, **COLOURS) is None
    unreported = CustomPropertySummary(
        property_breakdown=(
            PropertyUsage(property="beta:video", feed_count=None),
            PropertyUsage(property=" ", feed_count=4),
        ),
    )
    assert schema_drift.property_chart(unreported, TEAL, **COLOURS) is None
