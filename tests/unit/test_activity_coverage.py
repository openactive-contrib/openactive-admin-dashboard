"""The activity coverage monitor: rows, filters, figures, the ranking, the map and the card.

Every pure function gets a happy path, an empty-input case and the boundary most likely to be
quietly wrong — here, a count the batch reported as null (not a zero), and a total across
activities, which counts tags rather than opportunities.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fixture_loader import load_sample
from stewards.api.models import (
    ActivityCoverageSummary,
    ActivityCoverageSummaryResponse,
    ActivityDistrict,
    ActivityDistrictPage,
    TopActivity,
    TopActivityPage,
)
from stewards.geography import Boundaries, uk_local_authority_districts
from stewards.monitors import activity_coverage
from stewards.monitors.activity_coverage import ActivityRow
from stewards.monitors.choropleth import Choropleth, ChoroplethStyle, read_data_url
from stewards.monitors.health import HealthState, Movement
from stewards.monitors.overview import build_tiles, nav_badges, tile_label, tile_state
from stewards.monitors.registry import Source, get_monitor, monitor_ids
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import EMPTY

MONITOR = get_monitor("activity_coverage")

#: Pinned locally rather than imported from `theme`, so these assert behaviour, not hexes.
TEAL = "#0E8F8A"
COLOURS = {"label_colour": "#5C6B76", "grid_colour": "#EDF0F2"}
STYLE = ChoroplethStyle(
    ramp=("#D3EBE9", "#0A5F5C"),
    no_data="#CCCCCC",
    stroke="#FFFFFF",
    highlight="#112233",
    label_colour="#555555",
    tooltip_background="#FFFFFF",
    tooltip_text="#111111",
    tooltip_border="#DDDDDD",
    legend_title="Activity tags (log scale)",
)

BIRMINGHAM = "E08000025"
CARDIFF = "W06000015"


@pytest.fixture(scope="module")
def boundaries() -> Boundaries:
    return uk_local_authority_districts()


@pytest.fixture
def summary_data() -> ActivityCoverageSummary:
    return ActivityCoverageSummaryResponse.model_validate(
        load_sample("activity_coverage_summary")
    ).data


@pytest.fixture
def rows() -> tuple[ActivityRow, ...]:
    page = ActivityDistrictPage.model_validate(load_sample("activity_coverage_districts"))
    return activity_coverage.build_rows(page.data)


@pytest.fixture
def top() -> tuple[TopActivity, ...]:
    return TopActivityPage.model_validate(load_sample("activity_coverage_top_activities")).data


def district(**overrides: Any) -> ActivityDistrict:
    payload: dict[str, Any] = {
        "district_code": "E09000028",
        "district_name": "Southwark",
        "region_name": "London",
        "country_name": "England",
        "activity": "Yoga",
        "activity_id": "https://openactive.io/activity-list#yoga",
        "opportunity_count": 10,
    }
    payload.update(overrides)
    return ActivityDistrict.model_validate(payload)


def row(**overrides: Any) -> ActivityRow:
    return activity_coverage.build_rows([district(**overrides)])[0]


# --- rows ---------------------------------------------------------------------------------


def test_rows_carry_the_payload_fields(rows: tuple[ActivityRow, ...]) -> None:
    payload = load_sample("activity_coverage_districts")["data"]
    assert len(rows) == len(payload)
    first = rows[0]
    assert first.district_code == payload[0]["district_code"]
    assert first.activity == payload[0]["activity"]
    assert first.opportunity_count == payload[0]["opportunity_count"]


def test_no_districts_is_no_rows() -> None:
    assert activity_coverage.build_rows([]) == ()


def test_a_district_outside_england_takes_its_nation_as_its_region() -> None:
    """ONS regions divide England only: the batch sends null for Wales, which is not
    "Unknown"."""
    welsh = row(
        district_code=CARDIFF, district_name="Cardiff", region_name=None, country_name="Wales"
    )
    assert welsh.region_name == "Wales"


def test_blank_names_fall_back_rather_than_reading_blank() -> None:
    blank = row(district_name="", region_name="", country_name="", activity="")
    assert blank.district_name == "E09000028"
    assert blank.region_name == activity_coverage.UNKNOWN
    assert blank.activity == activity_coverage.UNKNOWN
    nameless = row(district_code="", district_name="")
    assert nameless.district_name == activity_coverage.UNKNOWN


def test_a_null_count_stays_none_not_zero() -> None:
    assert row(opportunity_count=None).opportunity_count is None


def test_rows_sort_most_opportunities_first_with_unreported_counts_last() -> None:
    shuffled = (
        row(opportunity_count=None, activity="A"),
        row(opportunity_count=5, activity="B"),
        row(opportunity_count=0, activity="C"),
        row(opportunity_count=50, activity="D"),
    )
    ordered = activity_coverage.sort_rows(MONITOR, shuffled)
    assert [r.activity for r in ordered] == ["D", "B", "C", "A"]


def test_ties_sort_by_district_then_activity() -> None:
    tied = (
        row(district_name="Zeta", activity="Yoga", opportunity_count=1),
        row(district_name="alpha", activity="Swimming", opportunity_count=1),
        row(district_name="alpha", activity="Badminton", opportunity_count=1),
    )
    ordered = activity_coverage.sort_rows(MONITOR, tied)
    assert [(r.district_name, r.activity) for r in ordered] == [
        ("alpha", "Badminton"),
        ("alpha", "Swimming"),
        ("Zeta", "Yoga"),
    ]


def test_sorting_nothing_is_nothing() -> None:
    assert activity_coverage.sort_rows(MONITOR, ()) == []


# --- filters ------------------------------------------------------------------------------


def test_filter_options_are_distinct_and_sorted_ignoring_case() -> None:
    rows = (row(activity="yoga"), row(activity="Badminton"), row(activity="yoga"))
    assert activity_coverage.filter_options(rows, "activity") == ["Badminton", "yoga"]


def test_filter_options_of_nothing_is_empty() -> None:
    assert activity_coverage.filter_options((), "activity") == []


def test_the_activity_and_the_district_narrow_together(rows: tuple[ActivityRow, ...]) -> None:
    swimming = activity_coverage.apply_filters(rows, selections={"activity": "Swimming"})
    assert swimming
    assert all(r.activity == "Swimming" for r in swimming)
    both = activity_coverage.apply_filters(
        rows, selections={"activity": "Swimming"}, district_code=BIRMINGHAM
    )
    assert [(r.district_code, r.activity) for r in both] == [(BIRMINGHAM, "Swimming")]


def test_no_selection_and_no_district_leave_every_row(rows: tuple[ActivityRow, ...]) -> None:
    assert activity_coverage.apply_filters(rows, selections={"activity": ""}) == list(rows)
    assert activity_coverage.apply_filters(rows) == list(rows)


def test_the_search_matches_part_of_a_name_ignoring_case(
    rows: tuple[ActivityRow, ...],
) -> None:
    found = activity_coverage.search_rows(rows, "  edINBurgh ")
    assert found
    assert {r.district_name for r in found} == {"City of Edinburgh"}


def test_a_blank_search_matches_everything_and_nothing_matches_nothing(
    rows: tuple[ActivityRow, ...],
) -> None:
    assert activity_coverage.search_rows(rows, "   ") == list(rows)
    assert activity_coverage.search_rows(rows, "Atlantis") == []
    assert activity_coverage.search_rows((), "Cardiff") == []


def test_the_search_combines_with_the_activity(rows: tuple[ActivityRow, ...]) -> None:
    both = activity_coverage.apply_filters(
        rows, search="card", selections={"activity": "Football"}
    )
    assert [(r.district_name, r.activity) for r in both] == [("Cardiff", "Football")]


def test_the_search_matches_the_name_not_the_region_or_activity(
    rows: tuple[ActivityRow, ...],
) -> None:
    assert activity_coverage.search_rows(rows, "London") == []
    assert activity_coverage.search_rows(rows, "Swimming") == []


def test_a_district_with_no_such_activity_is_no_rows(rows: tuple[ActivityRow, ...]) -> None:
    assert activity_coverage.apply_filters(rows, district_code="E00000000") == []


def test_a_district_is_named_by_its_rows_or_by_its_code(rows: tuple[ActivityRow, ...]) -> None:
    assert activity_coverage.district_label(rows, BIRMINGHAM) == "Birmingham"
    assert activity_coverage.district_label(rows, "E00000000") == "E00000000"
    assert activity_coverage.district_label(rows, None) is None
    assert activity_coverage.district_label((), "") is None


# --- the table ----------------------------------------------------------------------------


def test_the_frame_has_the_declared_columns_in_order(rows: tuple[ActivityRow, ...]) -> None:
    frame = activity_coverage.to_dataframe(MONITOR, rows)
    assert list(frame.columns) == [c.label for c in MONITOR.columns]
    assert len(frame) == len(rows)


def test_no_rows_is_an_empty_frame_with_the_declared_columns() -> None:
    frame = activity_coverage.to_dataframe(MONITOR, ())
    assert frame.empty
    assert list(frame.columns) == [c.label for c in MONITOR.columns]
    assert activity_coverage.tone_frame(MONITOR, ()).empty


def test_no_cell_is_shaded(rows: tuple[ActivityRow, ...]) -> None:
    """A count of tags is not a state, so nothing gets a RAG background."""
    tones = activity_coverage.tone_frame(MONITOR, rows)
    assert tones.shape == (len(rows), len(MONITOR.columns))
    assert set(tones.to_numpy().ravel()) == {""}


def test_the_table_caption_says_how_to_widen_it_again() -> None:
    narrowed = activity_coverage.table_caption(3, 5_167, district="Birmingham")
    assert narrowed.startswith("3 of 5,167 ")
    assert "Narrowed to Birmingham" in narrowed
    whole = activity_coverage.table_caption(0, 0, district=None)
    assert whole.startswith("0 of 0 ")
    assert "Filter to a local authority" in whole


# --- the figures --------------------------------------------------------------------------


def test_the_three_figures_come_off_the_summary(summary_data: ActivityCoverageSummary) -> None:
    stats = activity_coverage.page_stats(summary_data)
    assert [s.label for s in stats] == [
        "Opportunities with activity",
        "Labelled opportunities",
        "Activities offered",
    ]
    assert [s.value for s in stats] == ["683,255", "447,497", "288"]
    assert stats[1].sub.startswith("65.5% ")
    assert stats[2].sub == "of 768 in the activity list"
    assert all(s.tone is None for s in stats)


def test_an_unreported_summary_reads_em_dash_never_zero() -> None:
    stats = activity_coverage.page_stats(ActivityCoverageSummary())
    assert [s.value for s in stats] == [EMPTY] * 3
    assert stats[1].sub.startswith(EMPTY)


def test_the_linked_figures_are_read_but_not_shown(
    summary_data: ActivityCoverageSummary,
) -> None:
    """The model declares them so the monthly metric capture can record both shares. The
    page is unchanged: it still draws the labelled one only."""
    assert summary_data.linked_opportunities == 366272
    stats = activity_coverage.page_stats(summary_data)
    assert not any("366,272" in s.value or "53.6" in s.sub for s in stats)


# --- the card -----------------------------------------------------------------------------


def test_the_card_reads_as_information_not_as_a_rag_verdict(
    summary_data: ActivityCoverageSummary,
) -> None:
    health = activity_coverage.assess_activity_coverage(summary_data)
    assert health.state is HealthState.WARNING
    assert health.movement is Movement.UNKNOWN
    assert health.current == 65.5
    assert tile_state(MONITOR, health) is Tone.GREY
    assert tile_label(MONITOR, health) == "Info"


def test_an_unreported_share_is_unknown_not_an_all_clear() -> None:
    health = activity_coverage.assess_activity_coverage(ActivityCoverageSummary())
    assert health.state is HealthState.UNKNOWN
    assert (
        activity_coverage.tile_note(ActivityCoverageSummary()) == activity_coverage.NOT_REPORTED
    )


def test_the_card_is_one_headline_and_three_figures(
    summary_data: ActivityCoverageSummary,
) -> None:
    card = activity_coverage.tile_card(summary_data)
    assert card.value == "65.5%"
    assert card.unit == MONITOR.unit
    assert [(f.label, f.value) for f in card.facts] == [
        ("With activity", "683,255"),
        ("Labelled", "447,497"),
        ("Activities", "288 of 768"),
    ]
    assert card.badge is None
    assert "this snapshot only" in card.note


def test_the_card_survives_a_summary_with_no_figures() -> None:
    card = activity_coverage.tile_card(ActivityCoverageSummary())
    assert card.value == EMPTY
    assert [f.value for f in card.facts] == [EMPTY, EMPTY, f"{EMPTY} of {EMPTY}"]


def test_the_overview_draws_the_card_the_monitor_supplies(
    summary_data: ActivityCoverageSummary, summary
) -> None:
    cards = {MONITOR.id: activity_coverage.tile_card(summary_data)}
    tile = next(t for t in build_tiles(summary.data, {}, cards) if t.monitor is MONITOR)
    assert tile.value == "65.5%"
    assert tile.state_label == "Info"
    assert tile.trend_note == ""
    assert MONITOR.id not in nav_badges(summary.data, {}, cards)


def test_the_monitor_is_registered_as_its_own_read() -> None:
    assert MONITOR.source is Source.ACTIVITY_COVERAGE
    assert monitor_ids(Source.ACTIVITY_COVERAGE) == (MONITOR.id,)
    assert "activity tags" in MONITOR.blurb
    assert "counts once under each" in MONITOR.blurb


# --- the national ranking -----------------------------------------------------------------


def test_the_ranking_draws_every_activity_first_at_the_top(
    top: tuple[TopActivity, ...],
) -> None:
    chart = activity_coverage.top_activity_chart(top, TEAL, **COLOURS)
    assert chart is not None
    spec = chart.to_dict()
    (data,) = spec["datasets"].values()
    assert len(data) == 20
    assert data[0]["label"] == "Swimming"
    assert spec["encoding"]["y"]["sort"] is None
    assert spec["encoding"]["y"]["axis"]["labelOverlap"] is False
    assert spec["height"] == 20 * activity_coverage.BAR_STEP


def test_the_ranking_orders_by_rank_whatever_order_it_arrives_in() -> None:
    shuffled = (
        TopActivity(rank=2, activity="Gym", opportunity_count=5),
        TopActivity(rank=None, activity="Unranked", opportunity_count=100),
        TopActivity(rank=1, activity="Swim", opportunity_count=9),
    )
    chart = activity_coverage.top_activity_chart(shuffled, TEAL, **COLOURS)
    assert chart is not None
    (data,) = chart.to_dict()["datasets"].values()
    assert [d["label"] for d in data] == ["Swim", "Gym", "Unranked"]


def test_an_unnamed_or_uncounted_activity_is_left_out_and_nothing_is_no_chart() -> None:
    assert activity_coverage.top_activity_chart((), TEAL, **COLOURS) is None
    unusable = (
        TopActivity(rank=1, activity=" ", opportunity_count=3),
        TopActivity(rank=2, activity="Yoga", opportunity_count=None),
    )
    assert activity_coverage.top_activity_chart(unusable, TEAL, **COLOURS) is None


# --- the district map ---------------------------------------------------------------------


def test_all_activities_sum_to_tags_per_district(rows: tuple[ActivityRow, ...]) -> None:
    by_code = {r["district_code"]: r for r in activity_coverage.district_map_rows(rows)}
    birmingham = [r for r in rows if r.district_code == BIRMINGHAM]
    assert by_code[BIRMINGHAM]["opportunity_count"] == sum(
        r.opportunity_count or 0 for r in birmingham
    )
    assert by_code[BIRMINGHAM]["activity_count"] == len(birmingham)
    assert by_code[BIRMINGHAM]["name"] == "Birmingham"


def test_one_activity_maps_only_that_activity(rows: tuple[ActivityRow, ...]) -> None:
    mapped = activity_coverage.district_map_rows(rows, "Swimming")
    expected = {r.district_code: r.opportunity_count for r in rows if r.activity == "Swimming"}
    assert {m["district_code"]: m["opportunity_count"] for m in mapped} == expected


def test_a_district_with_only_null_counts_has_no_figure_not_zero() -> None:
    rows = (row(opportunity_count=None), row(opportunity_count=None, activity="Swimming"))
    (mapped,) = activity_coverage.district_map_rows(rows)
    assert mapped["opportunity_count"] is None
    assert mapped["activity_count"] == 2


def test_a_row_with_no_code_is_not_mapped_and_nothing_maps_nothing() -> None:
    assert activity_coverage.district_map_rows((row(district_code=""),)) == []
    assert activity_coverage.district_map_rows(()) == []


def test_the_tooltip_says_tags_across_activities_and_opportunities_for_one() -> None:
    every = activity_coverage.district_tooltip(None)
    one = activity_coverage.district_tooltip("Yoga")
    assert [f.title for f in every] == ["Region", "Activity tags", "Activities"]
    assert [f.title for f in one] == ["Region", "Opportunities"]
    assert activity_coverage.legend_title(None) == "Activity tags (log scale)"
    assert activity_coverage.legend_title("Yoga") == "Opportunities (log scale)"


def test_the_map_draws_the_whole_uk_on_a_log_scale(
    rows: tuple[ActivityRow, ...], boundaries: Boundaries
) -> None:
    figure = activity_coverage.district_map(rows, boundaries, STYLE)
    assert isinstance(figure, Choropleth)
    (layer,) = json.loads(figure.deck.to_json())["layers"]
    features = {
        f["properties"]["code"]: f["properties"]
        for f in read_data_url(layer["data"])["features"]
    }
    assert {BIRMINGHAM, CARDIFF} <= set(features)
    assert "Activity tags:" in features[CARDIFF]["tooltip"]
    legend_rows = [r for d in figure.legend.to_dict()["datasets"].values() for r in d]
    assert "1k" in [r.get("label") for r in legend_rows]


def test_an_activity_no_district_reports_is_no_map(boundaries: Boundaries) -> None:
    assert activity_coverage.district_map((), boundaries, STYLE) is None


def test_the_map_note_carries_the_tag_caveat_only_across_activities(
    rows: tuple[ActivityRow, ...], boundaries: Boundaries
) -> None:
    assert activity_coverage.map_note(rows, boundaries) == activity_coverage.TAG_NOTE
    assert activity_coverage.map_note(rows, boundaries, "Swimming") == ""


def test_a_code_the_boundaries_lack_is_named_not_dropped(boundaries: Boundaries) -> None:
    rows = (row(district_code="E99999999"),)
    assert activity_coverage.unmapped_districts(rows, boundaries) == ("E99999999",)
    assert "E99999999" in activity_coverage.map_note(rows, boundaries, "Yoga")


def test_every_live_district_code_is_on_the_bundled_boundaries(
    rows: tuple[ActivityRow, ...], boundaries: Boundaries
) -> None:
    assert activity_coverage.unmapped_districts(rows, boundaries) == ()
