"""The choropleth builder: join, colour, tooltip text, the opening view, the deck, the legend.

The boundaries are synthetic squares in degrees, so these run without the bundled file and
assert behaviour rather than geography.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stewards.geography import Boundaries, parse_boundaries
from stewards.monitors import choropleth
from stewards.monitors.choropleth import ChoroplethStyle, MapView, TooltipField

STYLE = ChoroplethStyle(
    ramp=("#000000", "#FFFFFF"),
    no_data="#CCCCCC",
    stroke="#FFFFFF",
    highlight="#112233",
    label_colour="#555555",
    tooltip_background="#FFFFFF",
    tooltip_text="#111111",
    tooltip_border="#DDDDDD",
    legend_title="Share %",
    height=400,
)
TOOLTIP = (TooltipField("share", "Share", ".1f", "%"), TooltipField("count", "Count", ","))


def boundaries(*codes: str, size: float = 1.0) -> Boundaries:
    """Squares `size` degrees across, side by side along the equator from 0°E."""
    return parse_boundaries(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"CD": code, "NM": f"Area {code}"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [i * size, 0],
                                [(i + 1) * size, 0],
                                [(i + 1) * size, size],
                                [i * size, 0],
                            ]
                        ],
                    },
                }
                for i, code in enumerate(codes)
            ],
        },
        code_field="CD",
        name_field="NM",
    )


def build(rows: list[dict[str, Any]], **kwargs: Any) -> choropleth.Choropleth | None:
    return choropleth.choropleth(
        boundaries("A", "B", "C"),
        rows,
        code_key="code_x",
        value_field="share",
        tooltip=TOOLTIP,
        style=STYLE,
        **kwargs,
    )


def spec_of(figure: choropleth.Choropleth) -> dict[str, Any]:
    return dict(json.loads(figure.deck.to_json()))


def features_of(figure: choropleth.Choropleth) -> dict[str, dict[str, Any]]:
    (layer,) = spec_of(figure)["layers"]
    document = choropleth.read_data_url(layer["data"])
    return {f["properties"]["code"]: f["properties"] for f in document["features"]}


# --- join ---------------------------------------------------------------------------------


def test_a_row_lands_in_its_areas_properties() -> None:
    joined = choropleth.join(
        boundaries("A", "B"), [{"code_x": "B", "share": 12.5}], code_key="code_x"
    )
    by_code = {f["properties"]["code"]: f["properties"] for f in joined.features}
    assert by_code["B"] == {"code": "B", "name": "Area B", "share": 12.5}
    assert by_code["A"] == {"code": "A", "name": "Area A"}
    assert joined.unmatched == ()


def test_a_row_name_overrides_the_boundary_name() -> None:
    joined = choropleth.join(
        boundaries("A"), [{"code_x": "A", "name": "Alpha"}], code_key="code_x"
    )
    assert joined.features[0]["properties"]["name"] == "Alpha"


def test_rows_no_boundary_carries_are_reported_not_dropped_silently() -> None:
    joined = choropleth.join(
        boundaries("A"),
        [{"code_x": "Z9"}, {"code_x": "A"}, {"code_x": "Y1"}, {"code_x": " "}, {}],
        code_key="code_x",
    )
    assert joined.unmatched == ("Y1", "Z9")
    assert len(joined.features) == 1


def test_joining_does_not_touch_the_boundaries() -> None:
    shapes = boundaries("A")
    choropleth.join(shapes, [{"code_x": "A", "share": 1.0}], code_key="code_x")
    assert shapes.features[0]["properties"] == {"code": "A", "name": "Area A"}


def test_joining_no_rows_leaves_every_area_without_a_figure() -> None:
    joined = choropleth.join(boundaries("A", "B"), [], code_key="code_x")
    assert all("share" not in f["properties"] for f in joined.features)


# --- colour -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], (0.0, 10.0)),
        ([None, None], (0.0, 10.0)),
        ([0.0, 0.0], (0.0, 10.0)),
        ([3.2, None, 40.0], (0.0, 40.0)),
        ([40.01], (0.0, 50.0)),
        ([0.5], (0.0, 10.0)),
    ],
)
def test_the_domain_runs_from_zero_to_the_top_value_rounded_up(
    values: list[float | None], expected: tuple[float, float]
) -> None:
    assert choropleth.value_domain(values) == expected


def test_the_domain_step_is_the_callers() -> None:
    assert choropleth.value_domain([72.0], step=25.0) == (0.0, 75.0)


def test_hex_becomes_rgb_and_a_malformed_one_is_refused() -> None:
    assert choropleth.rgb("#0E8F8A") == [14, 143, 138]
    assert choropleth.rgb("0e8f8a") == [14, 143, 138]
    with pytest.raises(ValueError, match="six-digit"):
        choropleth.rgb("#FFF")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, [0, 0, 0]),
        (50.0, [128, 128, 128]),
        (100.0, [255, 255, 255]),
        (-5.0, [0, 0, 0]),
        (150.0, [255, 255, 255]),
    ],
)
def test_the_ramp_interpolates_and_clamps(value: float, expected: list[int]) -> None:
    assert choropleth.ramp_colour(value, (0.0, 100.0), ("#000000", "#FFFFFF")) == expected


def test_a_multi_stop_ramp_runs_through_each_stop() -> None:
    ramp = ("#000000", "#FF0000", "#FFFFFF")
    assert choropleth.ramp_colour(50.0, (0.0, 100.0), ramp) == [255, 0, 0]
    assert choropleth.ramp_colour(75.0, (0.0, 100.0), ramp) == [255, 128, 128]


def test_a_one_stop_ramp_or_an_empty_domain_does_not_divide_by_zero() -> None:
    assert choropleth.ramp_colour(7.0, (0.0, 10.0), ("#123456",)) == [18, 52, 86]
    assert choropleth.ramp_colour(7.0, (5.0, 5.0), ("#000000", "#FFFFFF")) == [0, 0, 0]


# --- tooltip ------------------------------------------------------------------------------


def test_the_tooltip_is_the_name_then_one_formatted_line_per_field() -> None:
    text = choropleth.tooltip_text({"name": "Lewes", "share": 3.25, "count": 1234}, TOOLTIP)
    assert text == "Lewes\nShare: 3.2%\nCount: 1,234"


def test_a_missing_figure_reads_not_reported_never_zero() -> None:
    text = choropleth.tooltip_text({"name": "Lewes", "share": None}, TOOLTIP)
    assert text == "Lewes\nShare: Not reported\nCount: Not reported"


def test_a_value_that_is_not_a_number_is_not_formatted_as_one() -> None:
    assert choropleth.format_value("n/a", TOOLTIP[0]) == choropleth.NOT_REPORTED
    assert choropleth.format_value(True, TOOLTIP[0]) == choropleth.NOT_REPORTED
    assert choropleth.format_value("Ward 3", TooltipField("ward", "Ward")) == "Ward 3"


def test_an_area_without_a_name_is_headed_by_its_code() -> None:
    assert choropleth.tooltip_text({"code": "E1"}, ()) == "E1"


# --- the opening view ---------------------------------------------------------------------


def test_a_view_centres_on_the_extent_and_zooms_to_fit_it() -> None:
    view = choropleth.fit_view((0.0, 0.0, 10.0, 10.0), height=512, width=512)
    assert view.longitude == pytest.approx(5.0)
    assert view.latitude == pytest.approx(5.0, abs=0.05)
    # 10 degrees is 1/36 of the world; 512 px at 90% fill is zoom log2(36 * 0.9) ≈ 5.02.
    assert view.zoom == pytest.approx(5.02, abs=0.01)


def test_the_tighter_side_decides_the_zoom() -> None:
    wide = choropleth.fit_view((0.0, 0.0, 20.0, 1.0), height=512, width=512)
    tall = choropleth.fit_view((0.0, 0.0, 1.0, 20.0), height=512, width=512)
    assert wide.zoom == pytest.approx(
        choropleth.fit_view((0.0, 0.0, 20.0, 20.0), height=512, width=512).zoom, abs=0.05
    )
    assert tall.zoom < choropleth.fit_view((0.0, 0.0, 1.0, 1.0), height=512, width=512).zoom


def test_a_point_or_a_tiny_area_opens_at_the_maximum_zoom() -> None:
    assert choropleth.fit_view((1.0, 1.0, 1.0, 1.0), height=500).zoom == choropleth.MAX_ZOOM
    tiny = choropleth.fit_view((1.0, 1.0, 1.00001, 1.00001), height=500)
    assert tiny.zoom == choropleth.MAX_ZOOM


def test_the_whole_world_opens_at_the_minimum_zoom() -> None:
    assert choropleth.fit_view((-180, -80, 180, 80), height=500).zoom == choropleth.MIN_ZOOM


def test_focusing_on_an_area_zooms_in_on_it_with_room_around_it() -> None:
    shapes = boundaries("A", "B", "C", "D", "E", "F", "G", "H")
    whole = choropleth.view_for(shapes, None, height=400)
    close = choropleth.view_for(shapes, "C", height=400)
    assert close.zoom > whole.zoom
    assert close.longitude == pytest.approx(2.5)
    exact = choropleth.fit_view((2.0, 0.0, 3.0, 1.0), height=400)
    assert close.zoom == pytest.approx(exact.zoom - 1.585, abs=0.01)  # log2(3) out


def test_focusing_never_opens_wider_than_the_whole() -> None:
    shapes = boundaries("A")
    assert choropleth.view_for(shapes, "A", height=400).zoom == pytest.approx(
        choropleth.view_for(shapes, None, height=400).zoom
    )


def test_an_unknown_focus_or_no_areas_is_the_overview() -> None:
    shapes = boundaries("A", "B")
    assert choropleth.view_for(shapes, "Z", height=400) == choropleth.view_for(
        shapes, None, height=400
    )
    assert choropleth.view_for(boundaries(), None, height=400) == MapView(
        0.0, 0.0, choropleth.MIN_ZOOM
    )


# --- the map ------------------------------------------------------------------------------


def test_no_figure_anywhere_is_no_map() -> None:
    assert build([]) is None
    assert build([{"code_x": "A", "share": None}]) is None
    assert build([{"code_x": "Z", "share": 5.0}]) is None
    assert build([{"code_x": "A", "share": True}]) is None


def test_each_area_carries_its_colour_and_finished_tooltip_and_nothing_else() -> None:
    figure = build([{"code_x": "A", "share": 40.0, "count": 3}, {"code_x": "B", "share": 0.0}])
    assert figure is not None
    features = features_of(figure)
    assert features["A"] == {
        "code": "A",
        "name": "Area A",
        "fill": [255, 255, 255],
        "tooltip": "Area A\nShare: 40.0%\nCount: 3",
    }
    assert features["B"]["fill"] == [0, 0, 0]  # a genuine zero is the ramp's low end
    assert features["C"]["fill"] == [204, 204, 204]  # no figure is grey
    assert features["C"]["tooltip"] == "Area C\nShare: Not reported\nCount: Not reported"


def test_the_layer_is_pickable_highlights_on_hover_and_strokes_in_pixels() -> None:
    figure = build([{"code_x": "A", "share": 4.0}])
    assert figure is not None
    (layer,) = spec_of(figure)["layers"]
    assert layer["@@type"] == "GeoJsonLayer"
    assert layer["pickable"] is True
    assert layer["autoHighlight"] is True
    assert layer["highlightColor"] == [17, 34, 51, choropleth.HIGHLIGHT_ALPHA]
    assert layer["getFillColor"] == "@@=properties.fill"
    assert layer["getLineWidth"] == 0
    assert layer["lineWidthMinPixels"] == choropleth.STROKE_PX
    assert layer["data"].startswith(choropleth.DATA_URL_PREFIX)


def test_the_map_zooms_and_pans_but_never_rotates() -> None:
    figure = build([{"code_x": "A", "share": 4.0}])
    assert figure is not None
    spec = spec_of(figure)
    (view,) = spec["views"]
    controller = view["controller"]
    assert controller["scrollZoom"] is True
    assert controller["dragPan"] is True
    assert controller["doubleClickZoom"] is True
    assert controller["dragRotate"] is False
    assert controller["touchRotate"] is False
    state = spec["initialViewState"]
    assert (state["pitch"], state["bearing"]) == (0, 0)
    assert state["minZoom"] == choropleth.MIN_ZOOM
    assert state["maxZoom"] == choropleth.MAX_ZOOM


def test_the_tooltip_is_plain_text_in_the_callers_colours() -> None:
    """pydeck keeps the tooltip out of its JSON; Streamlit reads it off `_tooltip`."""
    figure = build([{"code_x": "A", "share": 4.0}])
    assert figure is not None
    tooltip = figure.deck._tooltip
    assert tooltip["text"] == "{tooltip}"
    assert "html" not in tooltip
    assert tooltip["style"]["backgroundColor"] == STYLE.tooltip_background
    assert tooltip["style"]["color"] == STYLE.tooltip_text


def test_the_map_opens_on_the_focus() -> None:
    whole = build([{"code_x": "A", "share": 4.0}])
    focused = build([{"code_x": "A", "share": 4.0}], focus="C")
    assert whole is not None
    assert focused is not None
    assert spec_of(focused)["initialViewState"]["longitude"] == pytest.approx(2.5)
    assert spec_of(whole)["initialViewState"]["longitude"] == pytest.approx(1.5)


def test_the_deck_spec_stays_small_because_the_features_are_a_data_url() -> None:
    """pydeck indents its JSON; inline features would put every coordinate on its own line."""
    figure = build([{"code_x": "A", "share": 4.0}])
    assert figure is not None
    assert figure.deck.to_json().count("\n") < 100


def test_a_data_url_round_trips_and_keeps_non_ascii() -> None:
    document = {"name": "Ynys Môn", "n": [1, 2]}
    assert choropleth.read_data_url(choropleth.data_url(document)) == document


def test_reading_anything_but_a_json_data_url_is_refused() -> None:
    with pytest.raises(ValueError, match="data URL"):
        choropleth.read_data_url("https://example.org/x.json")


# --- the legend ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ((0.0, 70.0), [0, 10, 20, 30, 40, 50, 60, 70]),
        ((0.0, 40.0), [0, 10, 20, 30, 40]),
        ((0.0, 10.0), [0, 2, 4, 6, 8, 10]),
        ((0.0, 3.0), [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]),
        ((5.0, 5.0), [5.0]),
    ],
)
def test_legend_ticks_are_round_and_few(
    domain: tuple[float, float], expected: list[float]
) -> None:
    assert choropleth.legend_ticks(domain) == expected


def test_the_legend_is_its_title_the_ramp_and_its_ticks_with_no_vega_axis() -> None:
    """`st.altair_chart` fits a chart including its axes into the declared size, which
    squeezes an axis-bearing bar to nothing; every part is therefore pixel-placed."""
    figure = build([{"code_x": "A", "share": 40.0}])
    assert figure is not None
    spec = figure.legend.to_dict()
    title, bar, ticks = spec["layer"]
    assert title["encoding"]["text"]["field"] == "title"
    assert bar["encoding"]["color"]["scale"] == {
        "domain": [0.0, 40.0],
        "range": list(STYLE.ramp),
    }
    assert bar["encoding"]["x"]["axis"] is None
    assert "value" in bar["encoding"]["y"]
    assert ticks["encoding"]["x"]["axis"] is None
    rows = [row for dataset in spec["datasets"].values() for row in dataset]
    assert {"title": "Share %"} in rows
    assert [row["label"] for row in rows if "label" in row] == ["0", "10", "20", "30", "40"]
