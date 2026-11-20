"""Boundary loading: the grid conversion, CRS handling, rounding, filtering, the bundled set."""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from fixture_loader import load_sample
from stewards import geography
from stewards.geography import CODE, NAME, bng_to_wgs84, crs_transform, parse_boundaries

BNG = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::27700"}}


def square(code: str, name: str = "", *, x: float = 0.0, extra: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {"CD": code, "NM": name}
    if extra:
        properties["BNG_E"] = 447161
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[x, 0.0], [x + 1.25, 0.0], [x + 1.25, 1.75], [x, 0.0]]],
        },
    }


def collection(*features: dict[str, Any], crs: dict[str, Any] | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {"type": "FeatureCollection", "features": list(features)}
    if crs is not None:
        document["crs"] = crs
    return document


def parse(document: dict[str, Any], **kwargs: Any) -> geography.Boundaries:
    return parse_boundaries(document, code_field="CD", name_field="NM", **kwargs)


# --- the grid conversion ------------------------------------------------------------------


def test_the_inverse_projection_matches_the_ordnance_surveys_worked_example() -> None:
    """OS guide, annexe C: E 651409.903, N 313177.270 is 52°39'27.2531"N 1°43'4.5177"E on
    OSGB36, before any datum shift."""
    lat, lon = geography._grid_to_osgb36(651409.903, 313177.270)
    assert math.degrees(lat) == pytest.approx(52 + 39 / 60 + 27.2531 / 3600, abs=1e-7)
    assert math.degrees(lon) == pytest.approx(1 + 43 / 60 + 4.5177 / 3600, abs=1e-7)


def test_a_known_landmark_lands_where_a_web_map_puts_it() -> None:
    """Big Ben, TQ 30268 79640, is at about 51.5007 N, 0.1246 W in WGS84."""
    lon, lat = bng_to_wgs84(530268, 179640)
    assert lat == pytest.approx(51.5007, abs=2e-4)
    assert lon == pytest.approx(-0.1246, abs=2e-4)


def test_every_english_centroid_agrees_with_the_one_ons_publishes() -> None:
    """ONS ships each district's centroid on the grid and in WGS84; the conversion must
    take one to the other to within about ten metres. Northern Ireland is not on the British
    grid and is not drawn, so it is left out."""
    raw = json.loads(geography.UK_LAD_2024.path.read_text(encoding="utf-8"))
    english = [f["properties"] for f in raw["features"] if f["properties"]["LAD24CD"][0] == "E"]
    assert len(english) == 296
    for props in english:
        lon, lat = bng_to_wgs84(props["BNG_E"], props["BNG_N"])
        assert lon == pytest.approx(props["LONG"], abs=1e-4), props["LAD24NM"]
        assert lat == pytest.approx(props["LAT"], abs=1e-4), props["LAD24NM"]


# --- CRS ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "crs",
    [
        None,
        {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        {"type": "name", "properties": {"name": "EPSG:4326"}},
        {"type": "name", "properties": {}},
        {"type": "name"},
    ],
)
def test_longitude_latitude_needs_no_transform(crs: dict[str, Any] | None) -> None:
    """RFC 7946 GeoJSON has no crs and is WGS84."""
    assert crs_transform(collection(crs=crs)) is None


def test_the_national_grid_is_converted() -> None:
    assert crs_transform(collection(crs=BNG)) is bng_to_wgs84


def test_any_other_crs_is_refused_rather_than_drawn_in_the_wrong_place() -> None:
    other = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2157"}}
    with pytest.raises(ValueError, match="unsupported CRS"):
        parse(collection(square("E1"), crs=other))


# --- parsing ------------------------------------------------------------------------------


def test_only_the_code_name_and_geometry_survive() -> None:
    (feature,) = parse(collection(square("E1", "Alpha", extra=True))).features
    assert feature["properties"] == {CODE: "E1", NAME: "Alpha"}
    assert feature["geometry"]["type"] == "Polygon"


def test_a_grid_file_comes_out_in_degrees() -> None:
    document = collection(
        {
            "type": "Feature",
            "properties": {"CD": "E1", "NM": "Westminster"},
            "geometry": {"type": "Point", "coordinates": [530268, 179640]},
        },
        crs=BNG,
    )
    (feature,) = parse(document).features
    lon, lat = feature["geometry"]["coordinates"]
    assert (lon, lat) == (round(lon, 4), round(lat, 4))
    assert lat == pytest.approx(51.5007, abs=2e-4)


def test_an_unnamed_area_is_named_by_its_code() -> None:
    assert parse(collection(square("E1"))).names == {"E1": "E1"}


def test_an_empty_collection_loads_as_no_areas() -> None:
    loaded = parse(collection())
    assert loaded.features == ()
    assert loaded.codes == ()


def test_positions_are_rounded_to_the_precision_asked_for() -> None:
    (feature,) = parse(collection(square("E1", x=0.123456)), precision=2).features
    assert feature["geometry"]["coordinates"][0][0] == [0.12, 0.0]


def test_precision_none_leaves_the_positions_alone() -> None:
    (feature,) = parse(collection(square("E1", x=0.123456)), precision=None).features
    assert feature["geometry"]["coordinates"][0][0] == [0.123456, 0.0]


@pytest.mark.parametrize(
    "document",
    [
        {"type": "Feature"},
        {"type": "FeatureCollection"},
        {"type": "FeatureCollection", "features": {}},
    ],
)
def test_anything_but_a_feature_collection_is_refused(document: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="FeatureCollection"):
        parse(document)


def test_a_feature_with_no_code_is_refused() -> None:
    with pytest.raises(ValueError, match="no CD"):
        parse(collection(square("  ")))


def test_a_feature_with_no_geometry_is_refused() -> None:
    feature = square("E1")
    feature["geometry"] = None
    with pytest.raises(ValueError, match="E1 has no geometry"):
        parse(collection(feature))


# --- filtering and extent -----------------------------------------------------------------


def test_only_narrows_by_prefix_and_by_code_and_both_together() -> None:
    loaded = parse(collection(square("E1"), square("E2"), square("S1"), square("W1")))
    assert loaded.only(prefixes=("E",)).codes == ("E1", "E2")
    assert loaded.only(prefixes=("S", "W")).codes == ("S1", "W1")
    assert loaded.only(codes={"E2", "S1"}).codes == ("E2", "S1")
    assert loaded.only(prefixes=("E",), codes={"E2", "S1"}).codes == ("E2",)
    assert loaded.only().codes == loaded.codes
    assert loaded.only(codes=()).codes == ()


def test_filtering_keeps_the_attribution() -> None:
    loaded = parse(collection(square("E1")), attribution="ONS")
    assert loaded.only(prefixes=("X",)).attribution == "ONS"


def test_the_extent_spans_every_area_or_only_the_named_ones() -> None:
    loaded = parse(collection(square("E1", x=0.0), square("E2", x=10.0)))
    assert loaded.extent() == (0.0, 0.0, 11.25, 1.75)
    assert loaded.extent(["E2"]) == (10.0, 0.0, 11.25, 1.75)


def test_the_extent_reads_multipolygons_as_deep_as_they_nest() -> None:
    feature = square("E1")
    feature["geometry"] = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 0]]],
            [[[5, -2], [6, -2], [6, 3], [5, -2]]],
        ],
    }
    assert parse(collection(feature)).extent() == (0.0, -2.0, 6.0, 3.0)


def test_no_areas_have_no_extent() -> None:
    assert parse(collection()).extent() is None
    assert parse(collection(square("E1"))).extent(["nope"]) is None


# --- the bundled set ----------------------------------------------------------------------


def test_the_bundled_districts_are_every_uk_district_in_degrees() -> None:
    districts = geography.uk_local_authority_districts()
    assert len(districts.features) == 361
    england = districts.only(prefixes=("E",))
    assert len(england.features) == 296
    west, south, east, north = england.extent() or (0, 0, 0, 0)
    # England, from the Scillies to Berwick and from Land's End to Lowestoft.
    assert -6.5 < west < -5.0
    assert 49.8 < south < 50.0
    assert 1.7 < east < 1.8
    assert 55.7 < north < 55.9
    assert "Open Government Licence" in districts.attribution
    assert "CARTO" in districts.attribution


def test_every_fixture_authority_has_a_boundary() -> None:
    reported = load_sample("active_places_coverage_coverage")["data"][
        "coverage_by_local_authority"
    ]
    codes = set(geography.uk_local_authority_districts().codes)
    assert reported
    assert {row["local_authority_code"] for row in reported} <= codes


def test_the_bundled_districts_are_read_once() -> None:
    assert geography.uk_local_authority_districts() is geography.uk_local_authority_districts()
