"""The Active Places coverage monitor: rows, figures, filters, the chart and the card.

The coverage source's counterpart to `test_feed_quality.py`. Every pure function gets a
happy path, an empty-input case and the boundary most likely to be quietly wrong — here,
a figure the batch reported as null, which is not a zero.
"""

from __future__ import annotations

import pytest

from fixture_loader import load_sample
from stewards.api.models import (
    CoverageGroup,
    CoverageHeadline,
    CoverageResponse,
    CoverageSnapshot,
    SiteMapping,
    SiteMappingPage,
)
from stewards.monitors import coverage
from stewards.monitors.health import HealthState, Movement
from stewards.monitors.overview import build_tiles, nav_badges, tile_label, tile_state
from stewards.monitors.registry import Col, ColKind, Source, get_monitor, monitor_ids
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import EMPTY

MONITOR = get_monitor("active_places_coverage")

#: Pinned locally rather than imported from `theme`, so these assert behaviour and not the
#: brand's current hexes — `test_theme.py` owns those.
TEAL = "#0E8F8A"
COLOURS = {"label_colour": "#5C6B76", "grid_colour": "#EDF0F2"}


@pytest.fixture
def snapshot() -> CoverageSnapshot:
    return CoverageResponse.model_validate(load_sample("active_places_coverage_coverage")).data


@pytest.fixture
def rows() -> tuple[coverage.MappingRow, ...]:
    page = SiteMappingPage.model_validate(load_sample("active_places_coverage_mappings"))
    return coverage.build_rows(page.data)


def mapping(**overrides: object) -> SiteMapping:
    """A pair with the fields a test cares about, and defaults for the rest."""
    payload: dict[str, object] = {
        "site_id": "1000001",
        "site_name": "TEST LEISURE CENTRE",
        "postcode": "AB1 2CD",
        "local_authority_name": "Testshire",
        "ownership_type_group": "Local Authority",
        "ap_facility_count": 3,
        "oa_location_names": ["Test Leisure Centre"],
        "oa_dataset_urls": ["https://data.example.org/"],
        "oa_publisher_names": ["Example"],
        "oa_postal_codes": ["AB1 2CD"],
        "oa_kinds": ["SessionSeries"],
        "oa_opportunity_count": 12,
        "distance_metres": 40.0,
        "match_method": "spatial",
        "name_similarity": None,
        "is_primary_for_venue": True,
        "is_mutual_best": True,
    }
    payload.update(overrides)
    return SiteMapping.model_validate(payload)


# --- rows ---------------------------------------------------------------------------------


def test_a_pair_becomes_a_row_carrying_the_scalars_the_table_needs(
    rows: tuple[coverage.MappingRow, ...],
) -> None:
    assert rows
    row = rows[0]
    assert row.site_name
    assert row.match_label in set(coverage.MATCH_LABELS.values()) or row.match_label
    assert row.publisher_names


def test_an_empty_payload_yields_no_rows_rather_than_raising() -> None:
    assert coverage.build_rows(()) == ()


def test_parallel_lists_are_joined_distinctly_and_kept_whole() -> None:
    """A venue cluster can carry several names and publishers; the cell holds one string
    and the panel still gets the list."""
    row = coverage.build_rows(
        (
            mapping(
                oa_publisher_names=["Playwaze", "England Netball", "Playwaze"],
                oa_kinds=["ScheduledSession", "SessionSeries"],
            ),
        )
    )[0]
    assert row.publisher_names == "Playwaze, England Netball"
    assert row.publishers == ("Playwaze", "England Netball", "Playwaze")
    assert row.kinds == "ScheduledSession, SessionSeries"


def test_a_venue_cluster_with_no_name_says_so_rather_than_reading_as_a_gap() -> None:
    assert coverage.build_rows((mapping(oa_location_names=[]),))[0].venue_name == (
        "Unnamed venue"
    )
    assert coverage.build_rows((mapping(oa_location_names=["  "]),))[0].venue_name == (
        "Unnamed venue"
    )


def test_a_blank_categorical_reads_as_unknown_so_it_stays_a_filter_option() -> None:
    row = coverage.build_rows(
        (mapping(local_authority_name="", ownership_type_group="", oa_publisher_names=[]),)
    )[0]
    assert row.local_authority_name == coverage.UNKNOWN
    assert row.ownership_type_group == coverage.UNKNOWN
    assert row.publisher_names == coverage.UNKNOWN


@pytest.mark.parametrize(
    ("method", "label"),
    [
        ("spatial", "Proximity"),
        ("spatial_and_postcode", "Proximity and postcode"),
        ("spatial_centroid_only", "Proximity, centroid only"),
        ("postcode", "Postcode"),
        ("name", "Name"),
        ("", coverage.UNKNOWN),
        ("some_future_channel", "Some future channel"),
    ],
)
def test_every_match_channel_reads_as_prose(method: str, label: str) -> None:
    assert coverage.match_label(method) == label


def test_a_similarity_becomes_a_percent_and_a_missing_one_stays_missing() -> None:
    """The boundary: the channels that are not the name channel report no similarity, which
    is not a similarity of zero."""
    assert coverage.as_percent(0.8) == pytest.approx(80.0)
    assert coverage.as_percent(1.0) == pytest.approx(100.0)
    assert coverage.as_percent(0.0) == pytest.approx(0.0)
    assert coverage.as_percent(None) is None


def test_the_fixture_reports_a_similarity_only_on_the_name_channel(
    rows: tuple[coverage.MappingRow, ...],
) -> None:
    scored = [row for row in rows if row.name_similarity_percent is not None]
    assert scored
    assert len(scored) < len(rows)


# --- sorting ------------------------------------------------------------------------------


def test_rows_order_by_the_declared_field_then_the_site_then_the_nearest_pair() -> None:
    rows = coverage.build_rows(
        (
            mapping(site_id="2", site_name="B SITE", oa_opportunity_count=5),
            mapping(site_id="1", site_name="A SITE", oa_opportunity_count=90),
            mapping(site_id="3", site_name="C SITE", oa_opportunity_count=90),
        )
    )
    ordered = coverage.sort_rows(MONITOR, rows)
    assert [row.site_name for row in ordered] == ["A SITE", "C SITE", "B SITE"]


def test_a_sites_own_pairs_stay_together_nearest_first() -> None:
    rows = coverage.build_rows(
        (
            mapping(site_id="1", oa_opportunity_count=10, distance_metres=180.0),
            mapping(site_id="1", oa_opportunity_count=10, distance_metres=20.0),
        )
    )
    assert [row.distance_metres for row in coverage.sort_rows(MONITOR, rows)] == [20.0, 180.0]


def test_a_pair_with_no_figure_sorts_after_every_pair_that_has_one() -> None:
    """The boundary: an unreported count must not conflate with a genuine zero."""
    rows = coverage.build_rows(
        (
            mapping(site_id="1", site_name="UNREPORTED", oa_opportunity_count=None),
            mapping(site_id="2", site_name="ZERO", oa_opportunity_count=0),
        )
    )
    assert [row.site_name for row in coverage.sort_rows(MONITOR, rows)] == [
        "ZERO",
        "UNREPORTED",
    ]


def test_sorting_an_empty_set_is_an_empty_list() -> None:
    assert coverage.sort_rows(MONITOR, ()) == []


# --- the table ----------------------------------------------------------------------------


def test_the_frame_carries_the_declared_columns_in_order(
    rows: tuple[coverage.MappingRow, ...],
) -> None:
    frame = coverage.to_dataframe(MONITOR, rows)
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert len(frame) == len(rows)


def test_an_empty_frame_still_declares_its_columns() -> None:
    frame = coverage.to_dataframe(MONITOR, ())
    assert list(frame.columns) == [col.label for col in MONITOR.columns]
    assert frame.empty


def test_an_unreported_cell_reads_em_dash_rather_than_a_zero() -> None:
    rows = coverage.build_rows((mapping(oa_opportunity_count=None, postcode=""),))
    frame = coverage.to_dataframe(MONITOR, rows)
    assert frame["Postcode"][0] == EMPTY
    assert frame["Opportunities"][0] is None


def test_only_the_rag_columns_are_toned(rows: tuple[coverage.MappingRow, ...]) -> None:
    tones = coverage.tone_frame(MONITOR, rows)
    assert list(tones.columns) == [col.label for col in MONITOR.columns]
    assert set(tones["Active Places site"]) == {""}
    assert set(tones["Name similarity"]) <= {t.value for t in Tone} | {""}


def test_a_full_name_match_shades_green_and_a_weak_one_red() -> None:
    col = MONITOR.column("Name similarity")
    assert coverage.cell_tone(col, 100.0) is Tone.GREEN
    assert coverage.cell_tone(col, 10.0) is Tone.RED
    assert coverage.cell_tone(col, None) is Tone.GREY
    assert coverage.cell_tone(MONITOR.column("Active Places site"), "x") is None


def test_a_risk_column_shades_the_other_way_up() -> None:
    """No coverage column declares one today; the mapping exists so one can, as it does on
    an incident table."""
    col = Col("share", "Share", ColKind.RISK)
    assert coverage.cell_tone(col, 60.0) is Tone.RED
    assert coverage.cell_tone(col, 0.0) is Tone.GREEN
    assert coverage.cell_tone(col, None) is Tone.GREY


def test_an_empty_tone_frame_still_declares_its_columns() -> None:
    assert list(coverage.tone_frame(MONITOR, ()).columns) == [
        col.label for col in MONITOR.columns
    ]


# --- filtering ----------------------------------------------------------------------------


def test_filter_options_are_the_distinct_values_present(
    rows: tuple[coverage.MappingRow, ...],
) -> None:
    options = coverage.filter_options(rows, "match_label")
    assert options == sorted(set(options))
    assert "Name" in options


def test_filter_options_of_an_empty_snapshot_are_empty() -> None:
    assert coverage.filter_options((), "match_label") == []


def test_search_matches_the_site_the_authority_the_venue_and_the_publisher() -> None:
    rows = coverage.build_rows(
        (
            mapping(site_name="RIVERSIDE POOL", oa_publisher_names=["GLL"]),
            mapping(site_name="HILLTOP GYM", oa_publisher_names=["Playwaze"]),
        )
    )
    assert len(coverage.search_rows(rows, "riverside")) == 1
    assert len(coverage.search_rows(rows, "playwaze")) == 1
    assert len(coverage.search_rows(rows, "Testshire")) == 2
    assert len(coverage.search_rows(rows, "   ")) == 2
    assert coverage.search_rows(rows, "nothing here") == []


def test_selections_narrow_and_an_empty_selection_does_not() -> None:
    rows = coverage.build_rows(
        (
            mapping(match_method="name"),
            mapping(match_method="spatial"),
        )
    )
    assert len(coverage.apply_filters(rows, selections={"match_label": "Name"})) == 1
    assert len(coverage.apply_filters(rows, selections={"match_label": ""})) == 2
    assert len(coverage.apply_filters(rows)) == 2


def test_the_primary_toggle_keeps_only_the_primary_pairs() -> None:
    rows = coverage.build_rows(
        (
            mapping(site_id="1", is_primary_for_venue=True),
            mapping(site_id="2", is_primary_for_venue=False),
        )
    )
    assert len(coverage.apply_filters(rows, primary_only=True)) == 1
    assert len(coverage.apply_filters(rows, primary_only=False)) == 2


def test_filtering_an_empty_snapshot_is_an_empty_list() -> None:
    assert coverage.apply_filters((), search="anything", primary_only=True) == []


# --- the figures --------------------------------------------------------------------------


def test_the_four_figures_come_off_the_headline(snapshot: CoverageSnapshot) -> None:
    stats = coverage.page_stats(snapshot)
    assert len(stats) == 4
    assert [stat.label for stat in stats] == [
        "Active Places coverage",
        "Sites matched",
        "Sites missing",
        "Venues not in Active Places",
    ]
    assert [stat.value for stat in stats] == ["26.4%", "7,351", "20,506", "8,565"]
    # Context, not a state: coverage is measured, not chased.
    assert all(stat.tone is None for stat in stats)
    assert all(stat.label and stat.sub for stat in stats)


def test_an_unreported_figure_reads_em_dash_with_no_tone() -> None:
    """The boundary that matters: a batch that computed nothing sent null, not zero."""
    stats = coverage.page_stats(CoverageSnapshot())
    assert [stat.value for stat in stats] == [EMPTY] * 4
    assert all(stat.tone is None for stat in stats)


def test_a_percent_and_a_count_format_or_read_em_dash() -> None:
    assert coverage.format_percent(26.4) == "26.4%"
    assert coverage.format_percent(0.0) == "0.0%"
    assert coverage.format_percent(None) == EMPTY
    assert coverage.format_number(27857) == "27,857"
    assert coverage.format_number(0) == "0"
    assert coverage.format_number(None) == EMPTY


# --- the verdict and the card ---------------------------------------------------------------


def test_the_verdict_claims_a_level_but_never_a_direction(
    snapshot: CoverageSnapshot,
) -> None:
    health = coverage.assess_coverage(snapshot)
    assert health.movement is Movement.UNKNOWN
    assert health.points == 0
    assert health.current == 26.4
    assert "26.4%" in health.reason


def test_the_card_reads_as_information_not_as_a_rag_verdict(
    snapshot: CoverageSnapshot,
) -> None:
    """An informational monitor renders a warning grey, which is the whole point: there is
    no defensible number that makes 26% good or bad."""
    health = coverage.assess_coverage(snapshot)
    assert health.state is HealthState.WARNING
    assert tile_state(MONITOR, health) is Tone.GREY
    assert tile_label(MONITOR, health) == "Info"


def test_an_unreported_coverage_figure_is_unknown_not_an_all_clear() -> None:
    health = coverage.assess_coverage(CoverageSnapshot())
    assert health.state is HealthState.UNKNOWN
    assert health.movement is Movement.UNKNOWN
    assert coverage.tile_note(CoverageSnapshot()) == coverage.NOT_REPORTED


def test_the_card_states_the_rate_its_supporting_figures_and_no_badge(
    snapshot: CoverageSnapshot,
) -> None:
    card = coverage.tile_card(snapshot)
    assert card.value == "26.4%"
    assert card.unit == MONITOR.unit
    assert [fact.value for fact in card.facts] == ["7,351", "20,506", "8,565"]
    # No sidebar pill: a coverage gap is not a queue to work through.
    assert card.badge is None
    assert "27,857 sites" in card.note


def test_the_card_survives_a_snapshot_with_no_figures_at_all() -> None:
    card = coverage.tile_card(CoverageSnapshot())
    assert card.value == EMPTY
    assert [fact.value for fact in card.facts] == [EMPTY] * 3
    assert card.badge is None


# --- the selected pair -----------------------------------------------------------------------


def test_the_pair_panel_states_how_the_match_was_made() -> None:
    row = coverage.build_rows((mapping(match_method="name", name_similarity=0.9),))[0]
    facts = dict(coverage.pair_facts(row))
    assert facts["Matched by"] == "Name"
    assert facts["Distance"] == "40 m"
    assert facts["Name similarity"] == "90%"
    assert facts["Primary pair for this venue"] == "yes"
    assert facts["Mutually nearest"] == "yes"


def test_the_pair_panel_reads_em_dash_where_the_batch_reported_nothing() -> None:
    row = coverage.build_rows(
        (
            mapping(
                distance_metres=None,
                ap_facility_count=None,
                oa_opportunity_count=None,
                oa_postal_codes=[],
                is_primary_for_venue=False,
                is_mutual_best=False,
            ),
        )
    )[0]
    facts = dict(coverage.pair_facts(row))
    assert facts["Distance"] == EMPTY
    assert facts["Name similarity"] == EMPTY
    assert facts["Venue postcodes"] == EMPTY
    assert facts["Primary pair for this venue"] == "no"


# --- the chart ------------------------------------------------------------------------------


def test_the_region_chart_draws_every_reported_region_best_first(
    snapshot: CoverageSnapshot,
) -> None:
    chart = coverage.region_chart(snapshot, TEAL, **COLOURS)
    assert chart is not None
    values = list(chart.data["value"])
    assert len(values) == 9
    assert values == sorted(values, reverse=True)
    assert next(iter(chart.data["label"])) == "London"


def test_a_chart_with_nothing_to_draw_returns_none_rather_than_an_empty_axis() -> None:
    assert coverage.region_chart(CoverageSnapshot(), TEAL, **COLOURS) is None
    unreported = CoverageSnapshot(
        coverage_by_region=(CoverageGroup(region_name="London", coverage_pct=None),)
    )
    assert coverage.region_chart(unreported, TEAL, **COLOURS) is None
    unnamed = CoverageSnapshot(
        coverage_by_region=(CoverageGroup(region_name=" ", coverage_pct=12.0),)
    )
    assert coverage.region_chart(unnamed, TEAL, **COLOURS) is None


# --- the overview ----------------------------------------------------------------------------


def test_the_monitor_is_registered_as_a_coverage_read_and_names_its_rows() -> None:
    assert MONITOR.source is Source.COVERAGE
    assert MONITOR.id in monitor_ids(Source.COVERAGE)
    assert MONITOR.id not in monitor_ids(Source.INCIDENTS)
    assert MONITOR.rows_id == "active_places_site_mappings"


def test_the_overview_draws_the_card_the_monitor_supplies(
    snapshot: CoverageSnapshot, summary
) -> None:
    """The monitor has no `/summary` entry at all, so without its own card the tile would
    read zero rather than 26.4%."""
    cards = {MONITOR.id: coverage.tile_card(snapshot)}
    tile = next(t for t in build_tiles(summary.data, {}, cards) if t.monitor is MONITOR)
    assert tile.value == "26.4%"
    assert tile.unit == MONITOR.unit
    assert tile.state is Tone.GREY
    assert tile.state_label == "Info"
    assert tile.sparkline == ()
    # One snapshot says nothing about direction, so the card claims no trend.
    assert tile.trend_note == ""


def test_the_monitor_gets_no_sidebar_pill(snapshot: CoverageSnapshot, summary) -> None:
    cards = {MONITOR.id: coverage.tile_card(snapshot)}
    assert MONITOR.id not in nav_badges(summary.data, {}, cards)


def test_a_headline_without_a_rate_still_builds_a_tile(summary) -> None:
    empty = CoverageSnapshot(headline=CoverageHeadline(sites_total=10))
    cards = {MONITOR.id: coverage.tile_card(empty)}
    tile = next(t for t in build_tiles(summary.data, {}, cards) if t.monitor is MONITOR)
    assert tile.value == EMPTY
    assert tile.state is Tone.GREY
