"""The schema drift monitor: rows, filters, figures, the property table, charts and the card.

The custom-property source's counterpart to `test_feed_quality.py`. Every pure function gets
a happy path, an empty-input case and the boundary most likely to be quietly wrong — here, a
figure the batch reported as null, which is not a zero, and a list-valued filter, which must
match a feed using the value anywhere rather than only where it is the whole field.
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
from stewards.monitors.health import HealthState, Movement
from stewards.monitors.overview import build_tiles, nav_badges
from stewards.monitors.registry import Source, get_monitor, monitor_ids
from stewards.monitors.schema_drift import PropertyRow
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import EMPTY

MONITOR = get_monitor("feed_custom_properties")

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


def test_an_empty_snapshot_yields_no_rows_and_an_empty_frame_with_the_columns() -> None:
    assert schema_drift.build_rows(()) == ()
    frame = schema_drift.to_dataframe(MONITOR, ())
    assert frame.empty
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert schema_drift.tone_frame(MONITOR, ()).empty


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
    frame = schema_drift.to_dataframe(MONITOR, (result,))
    assert pd.isna(frame.iloc[0]["Custom properties"])


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


# --- sorting and the table ----------------------------------------------------------------


def test_rows_sort_most_custom_properties_first(rows: tuple[PropertyRow, ...]) -> None:
    ordered = schema_drift.sort_rows(MONITOR, rows)
    counts = [r.property_count for r in ordered if r.property_count is not None]
    assert counts == sorted(counts, reverse=True)
    assert ordered[0].publisher_name == "British Cycling"


def test_a_row_reporting_no_count_sorts_after_a_reported_one() -> None:
    unreported = row(
        num_custom_properties=None, custom_properties=None, publisher_name="Aardvark"
    )
    one = row(
        num_custom_properties=1,
        custom_properties=[{"property": "beta:x", "namespace": "beta"}],
        publisher_name="Zebra",
    )
    assert schema_drift.sort_rows(MONITOR, (unreported, one)) == [one, unreported]


def test_the_properties_column_lists_each_property_once(
    rows: tuple[PropertyRow, ...],
) -> None:
    """A property used on several entity types is one name in the cell, not several."""
    frame = schema_drift.to_dataframe(MONITOR, rows)
    for cell, found in zip(frame["Properties"], rows, strict=True):
        if found.property_names:
            assert cell.split(", ") == list(found.property_names)
    triathlon = frame[frame["Publisher"] == "British Triathlon"].iloc[0]
    assert "btf:raceTypes" in triathlon["Properties"]
    assert triathlon["Properties"].count("beta:distance") == 1


def test_the_frame_carries_every_declared_column_in_order(
    rows: tuple[PropertyRow, ...],
) -> None:
    frame = schema_drift.to_dataframe(MONITOR, rows)
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert len(frame) == len(rows)
    tones = schema_drift.tone_frame(MONITOR, rows)
    assert tones.shape == frame.shape
    # No column here carries a RAG meaning, so nothing is shaded.
    assert set(tones.to_numpy().ravel()) == {""}


# --- filtering ----------------------------------------------------------------------------


def test_list_field_options_are_every_member_on_any_row(
    rows: tuple[PropertyRow, ...],
) -> None:
    assert schema_drift.filter_options(rows, "namespaces") == [
        "beta",
        "britishcycling",
        "btf",
        schema_drift.UNPREFIXED,
    ]
    properties = schema_drift.filter_options(rows, "property_names")
    assert "btf:raceTypes" in properties
    assert properties == sorted(properties, key=str.lower)


def test_scalar_field_options_are_the_distinct_values(rows: tuple[PropertyRow, ...]) -> None:
    options = schema_drift.filter_options(rows, "feed_type")
    assert schema_drift.UNKNOWN in options
    assert len(options) == len(set(options))


def test_filter_options_of_nothing_are_empty() -> None:
    assert schema_drift.filter_options((), "namespaces") == []


def test_a_list_field_selection_matches_a_feed_using_the_value_anywhere(
    rows: tuple[PropertyRow, ...],
) -> None:
    shown = schema_drift.apply_filters(rows, selections={"namespaces": "btf"})
    assert [r.publisher_name for r in shown] == ["British Triathlon"]
    beta = schema_drift.apply_filters(rows, selections={"namespaces": "beta"})
    assert len(beta) == sum(1 for r in rows if "beta" in r.namespaces)


def test_a_scalar_selection_matches_exactly(rows: tuple[PropertyRow, ...]) -> None:
    shown = schema_drift.apply_filters(rows, selections={"feed_type": "SessionSeries"})
    assert shown
    assert all(r.feed_type == "SessionSeries" for r in shown)


def test_an_empty_selection_is_no_filter(rows: tuple[PropertyRow, ...]) -> None:
    assert schema_drift.apply_filters(rows, selections={"namespaces": ""}) == list(rows)


def test_search_matches_a_property_name(rows: tuple[PropertyRow, ...]) -> None:
    shown = schema_drift.apply_filters(rows, search="GPXFILE")
    assert [r.publisher_name for r in shown] == ["British Cycling"]
    assert schema_drift.apply_filters(rows, search="  ") == list(rows)
    assert schema_drift.apply_filters(rows, search="no-such-thing") == []


def test_the_outside_beta_toggle_keeps_only_feeds_beta_does_not_cover(
    rows: tuple[PropertyRow, ...],
) -> None:
    shown = schema_drift.apply_filters(rows, outside_beta_only=True)
    assert shown
    assert len(shown) < len(rows)
    assert all(r.outside_beta_count for r in shown)


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


def test_the_verdict_is_informational_and_claims_no_movement(
    snapshot: CustomPropertyResponse,
) -> None:
    health = schema_drift.assess_drift(snapshot.summary)
    assert health.state is HealthState.WARNING  # rendered as grey "Info" on this monitor
    assert health.movement is Movement.UNKNOWN
    assert health.current == 62.0
    assert "62 of 159 datasets" in health.reason


def test_an_unreported_dataset_count_is_no_data_not_an_all_clear() -> None:
    health = schema_drift.assess_drift(CustomPropertySummary(feeds_with_custom_properties=3))
    assert health.state is HealthState.UNKNOWN
    assert health.reason == schema_drift.NOT_REPORTED


def test_a_zero_dataset_count_is_still_a_reported_figure() -> None:
    """Zero is information: the card states it, rather than reading as not reported."""
    card = schema_drift.tile_card(
        CustomPropertySummary(datasets_with_custom_properties=0, datasets_assessed=10)
    )
    assert card.value == "0"
    assert card.health.state is HealthState.WARNING
    assert card.note == "of 10 datasets assessed · this snapshot only"


def test_the_card_states_the_three_figures_the_monitor_is_about(
    snapshot: CustomPropertyResponse,
) -> None:
    card = schema_drift.tile_card(snapshot.summary)
    assert card.value == "62"
    assert card.unit == MONITOR.unit
    assert {fact.label: fact.value for fact in card.facts} == {
        "Feeds": "161",
        "Distinct properties": "57",
        "Publishers": "61",
    }
    assert card.badge is None


def test_an_unreported_card_says_so() -> None:
    card = schema_drift.tile_card(CustomPropertySummary())
    assert card.value == EMPTY
    assert card.note == schema_drift.NOT_REPORTED
    assert all(fact.value == EMPTY for fact in card.facts)


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


# --- the overview -------------------------------------------------------------------------


def test_the_monitor_is_registered_as_a_schema_drift_read() -> None:
    assert MONITOR.source is Source.SCHEMA_DRIFT
    assert MONITOR.id in monitor_ids(Source.SCHEMA_DRIFT)
    assert MONITOR.id not in monitor_ids(Source.INCIDENTS)


def test_the_overview_draws_the_card_the_monitor_supplies(
    snapshot: CustomPropertyResponse, summary
) -> None:
    """The monitor has no `/summary` entry, so without its own card the tile would read zero."""
    cards = {MONITOR.id: schema_drift.tile_card(snapshot.summary)}
    tile = next(t for t in build_tiles(summary.data, {}, cards) if t.monitor is MONITOR)
    assert tile.value == "62"
    assert tile.unit == MONITOR.unit
    assert tile.state is Tone.GREY
    assert tile.state_label == "Info"
    assert tile.sparkline == ()
    assert tile.trend_note == ""


def test_the_monitor_gets_no_sidebar_pill(snapshot: CustomPropertyResponse, summary) -> None:
    cards = {MONITOR.id: schema_drift.tile_card(snapshot.summary)}
    assert MONITOR.id not in nav_badges(summary.data, {}, cards)
