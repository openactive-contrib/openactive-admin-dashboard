"""Area boundaries for the app's maps. Streamlit-free.

A map needs the shapes in one coordinate system, and this module supplies them that way. It
reads a GeoJSON FeatureCollection, keeps only each feature's code, name and geometry, and
returns WGS84 longitude/latitude whatever the file was in: the map is a web map (deck.gl over
a tiled basemap), and a web map speaks nothing else. `monitors/choropleth.py` draws from what
it returns.

The one bundled set is the ONS Local Authority Districts, December 2024, for the whole UK:
the file `openactive-contrib/openactive-monitor` uses at
`volume-1/data-analysis/000-location-districts.geojson`, copied byte-for-byte so the two
projects draw the same districts. Its codes (`LAD24CD`) are the ones the coverage batch
reports. It is in EPSG:27700 (British National Grid, metres), so it is converted on load with
the Ordnance Survey's own formulas — inverse Transverse Mercator on the Airy 1830 ellipsoid,
then the published seven-parameter Helmert shift from OSGB36 to WGS84 — which are good to a
few metres, far inside what a generalised district boundary resolves, and need no geospatial
dependency. Each ONS feature carries its centroid in both systems, and the tests hold the
conversion to them.

It lives here rather than in `monitors/` because it reads a file: `monitors/` holds pure
functions, and the boundaries reach them as an argument.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ASSET_DIR = Path(__file__).resolve().parent / "assets"

#: The keys every loaded feature's properties are normalised to, whatever the source file
#: calls them.
CODE = "code"
NAME = "name"

#: Decimal places kept on a longitude or latitude: 4 is about 10 m, finer than any
#: generalised boundary, and every further place is payload the browser draws for nothing.
LONLAT_PRECISION = 4

#: West, south, east, north, in degrees.
Extent = tuple[float, float, float, float]

#: A position transform: (x, y) in the file's system -> (longitude, latitude).
Transform = Callable[[float, float], tuple[float, float]]


# --- British National Grid -> WGS84 -------------------------------------------------------
#
# Constants from the Ordnance Survey's "A Guide to Coordinate Systems in Great Britain".

_AIRY_A, _AIRY_B = 6377563.396, 6356256.909
_GRS80_A, _GRS80_B = 6378137.000, 6356752.3141
_F0 = 0.9996012717
_LAT0, _LON0 = math.radians(49.0), math.radians(-2.0)
_N0, _E0 = -100000.0, 400000.0

#: OSGB36 -> WGS84: translation in metres, scale in ppm, rotations in arc-seconds.
_TX, _TY, _TZ = 446.448, -125.157, 542.060
_S = -20.4894e-6
_RX, _RY, _RZ = (math.radians(arcsec / 3600) for arcsec in (0.1502, 0.2470, 0.8421))


def _meridional_arc(lat: float) -> float:
    n = (_AIRY_A - _AIRY_B) / (_AIRY_A + _AIRY_B)
    d, s = lat - _LAT0, lat + _LAT0
    return (
        _AIRY_B
        * _F0
        * (
            (1 + n + 1.25 * n**2 + 1.25 * n**3) * d
            - (3 * n + 3 * n**2 + 2.625 * n**3) * math.sin(d) * math.cos(s)
            + (1.875 * n**2 + 1.875 * n**3) * math.sin(2 * d) * math.cos(2 * s)
            - (35 / 24) * n**3 * math.sin(3 * d) * math.cos(3 * s)
        )
    )


def _grid_to_osgb36(easting: float, northing: float) -> tuple[float, float]:
    """Inverse Transverse Mercator: grid metres -> OSGB36 latitude, longitude (radians)."""
    a, b = _AIRY_A, _AIRY_B
    e2 = 1 - b**2 / a**2
    lat, arc = _LAT0, 0.0
    while abs(northing - _N0 - arc) >= 1e-5:
        lat += (northing - _N0 - arc) / (a * _F0)
        arc = _meridional_arc(lat)

    sin_lat, cos_lat, tan_lat = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * _F0 / math.sqrt(1 - e2 * sin_lat**2)
    rho = a * _F0 * (1 - e2) / (1 - e2 * sin_lat**2) ** 1.5
    eta2 = nu / rho - 1
    sec = 1 / cos_lat
    t2, t4, t6 = tan_lat**2, tan_lat**4, tan_lat**6

    vii = tan_lat / (2 * rho * nu)
    viii = tan_lat / (24 * rho * nu**3) * (5 + 3 * t2 + eta2 - 9 * t2 * eta2)
    ix = tan_lat / (720 * rho * nu**5) * (61 + 90 * t2 + 45 * t4)
    x = sec / nu
    xi = sec / (6 * nu**3) * (nu / rho + 2 * t2)
    xii = sec / (120 * nu**5) * (5 + 28 * t2 + 24 * t4)
    xiia = sec / (5040 * nu**7) * (61 + 662 * t2 + 1320 * t4 + 720 * t6)

    de = easting - _E0
    return (
        lat - vii * de**2 + viii * de**4 - ix * de**6,
        _LON0 + x * de - xi * de**3 + xii * de**5 - xiia * de**7,
    )


def _to_cartesian(lat: float, lon: float, a: float, b: float) -> tuple[float, float, float]:
    e2 = 1 - b**2 / a**2
    nu = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return (
        nu * math.cos(lat) * math.cos(lon),
        nu * math.cos(lat) * math.sin(lon),
        (1 - e2) * nu * math.sin(lat),
    )


def _from_cartesian(x: float, y: float, z: float, a: float, b: float) -> tuple[float, float]:
    e2 = 1 - b**2 / a**2
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        nu = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        lat = math.atan2(z + e2 * nu * math.sin(lat), p)
    return lat, math.atan2(y, x)


def bng_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """A British National Grid position as WGS84 (longitude, latitude) in degrees."""
    lat, lon = _grid_to_osgb36(easting, northing)
    x, y, z = _to_cartesian(lat, lon, _AIRY_A, _AIRY_B)
    scale = 1 + _S
    wx = _TX + scale * x - _RZ * y + _RY * z
    wy = _TY + _RZ * x + scale * y - _RX * z
    wz = _TZ - _RY * x + _RX * y + scale * z
    lat, lon = _from_cartesian(wx, wy, wz, _GRS80_A, _GRS80_B)
    return math.degrees(lon), math.degrees(lat)


# --- CRS ----------------------------------------------------------------------------------

#: CRS names GeoJSON uses for plain longitude/latitude, and for the national grid.
_LONLAT_CRS = ("CRS84", "EPSG:4326", "EPSG::4326")
_BNG_CRS = ("EPSG:27700", "EPSG::27700")


def crs_transform(geojson: Mapping[str, Any]) -> Transform | None:
    """None for longitude/latitude already, the grid conversion for British National Grid.

    RFC 7946 dropped the `crs` member and fixed GeoJSON to WGS84, so a file without one is
    longitude/latitude. ONS exports still carry one. Any other CRS raises: drawing metres as
    degrees would put the map in the Gulf of Guinea, silently.
    """
    crs = geojson.get("crs")
    if not isinstance(crs, Mapping):
        return None
    properties = crs.get("properties")
    name = str(properties.get("name", "")) if isinstance(properties, Mapping) else ""
    upper = name.upper()
    if not name or upper.endswith(_LONLAT_CRS):
        return None
    if upper.endswith(_BNG_CRS):
        return bng_to_wgs84
    raise ValueError(f"unsupported CRS {name}: expected WGS84 or British National Grid")


# --- boundaries ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Boundaries:
    """A loaded boundary set: GeoJSON features in WGS84 with `code` and `name` properties
    only."""

    features: tuple[dict[str, Any], ...]
    attribution: str = ""

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(feature["properties"][CODE] for feature in self.features)

    @property
    def names(self) -> dict[str, str]:
        return {
            properties[CODE]: properties[NAME]
            for properties in (feature["properties"] for feature in self.features)
        }

    def extent(self, codes: Iterable[str] | None = None) -> Extent | None:
        """The bounding box of every area, or of the areas in `codes`. None when that is no
        area at all."""
        wanted = frozenset(codes) if codes is not None else None
        xs: list[float] = []
        ys: list[float] = []
        for feature in self.features:
            if wanted is not None and feature["properties"][CODE] not in wanted:
                continue
            for x, y in _points(feature["geometry"]["coordinates"]):
                xs.append(x)
                ys.append(y)
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    def only(
        self,
        *,
        prefixes: Iterable[str] | None = None,
        codes: Iterable[str] | None = None,
    ) -> Boundaries:
        """The features whose code starts with one of `prefixes` and is one of `codes`.

        Either filter left as None does not narrow. ONS codes lead with the nation (`E`
        England, `S` Scotland, `W` Wales, `N` Northern Ireland), so `prefixes=("E",)` is
        England.
        """
        starts = tuple(prefixes) if prefixes is not None else None
        wanted = frozenset(codes) if codes is not None else None
        kept = tuple(
            feature
            for feature in self.features
            if (starts is None or feature["properties"][CODE].startswith(starts))
            and (wanted is None or feature["properties"][CODE] in wanted)
        )
        return dataclasses.replace(self, features=kept)


def _points(coordinates: Any) -> Iterable[tuple[float, float]]:
    """Every position in a nested coordinate array, however deep the geometry nests it."""
    if coordinates and isinstance(coordinates[0], int | float):
        yield (float(coordinates[0]), float(coordinates[1]))
        return
    for part in coordinates:
        yield from _points(part)


def _convert(coordinates: Any, transform: Transform | None, precision: int | None) -> Any:
    """Every position in a nested coordinate array, transformed and then rounded."""
    if coordinates and isinstance(coordinates[0], int | float):
        x, y = float(coordinates[0]), float(coordinates[1])
        if transform is not None:
            x, y = transform(x, y)
        if precision is not None:
            x, y = round(x, precision), round(y, precision)
        return [x, y]
    return [_convert(part, transform, precision) for part in coordinates]


def parse_boundaries(
    geojson: Mapping[str, Any],
    *,
    code_field: str,
    name_field: str,
    precision: int | None = LONLAT_PRECISION,
    attribution: str = "",
) -> Boundaries:
    """A FeatureCollection as WGS84 `Boundaries`.

    Every other property is dropped: the map ships the features to the browser, and a field
    it never reads is payload for nothing. Raises `ValueError` on anything that is not a
    FeatureCollection, on a CRS it cannot convert, and on a feature with no code or no
    geometry — a map silently missing an area is worse than one that refuses to load.
    """
    if geojson.get("type") != "FeatureCollection" or not isinstance(
        geojson.get("features"), list
    ):
        raise ValueError("expected a GeoJSON FeatureCollection")
    transform = crs_transform(geojson)
    features: list[dict[str, Any]] = []
    for index, feature in enumerate(geojson["features"]):
        properties = feature.get("properties") or {}
        code = str(properties.get(code_field) or "").strip()
        geometry = feature.get("geometry")
        if not code:
            raise ValueError(f"feature {index} has no {code_field}")
        if not isinstance(geometry, Mapping) or "coordinates" not in geometry:
            raise ValueError(f"feature {code} has no geometry")
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": geometry["type"],
                    "coordinates": _convert(geometry["coordinates"], transform, precision),
                },
                "properties": {
                    CODE: code,
                    NAME: str(properties.get(name_field) or code).strip(),
                },
            }
        )
    return Boundaries(tuple(features), attribution)


@dataclass(frozen=True, slots=True)
class BoundarySet:
    """A bundled boundary file and how to read it."""

    path: Path
    code_field: str
    name_field: str
    attribution: str
    source: str


def load_boundaries(boundary_set: BoundarySet) -> Boundaries:
    return parse_boundaries(
        json.loads(boundary_set.path.read_text(encoding="utf-8")),
        code_field=boundary_set.code_field,
        name_field=boundary_set.name_field,
        attribution=boundary_set.attribution,
    )


LAD_ATTRIBUTION = (
    "Boundaries: ONS Local Authority Districts, December 2024. Source: Office for National "
    "Statistics licensed under the Open Government Licence v3.0. Contains OS data © Crown "
    "copyright and database right 2024. Basemap © CARTO, © OpenStreetMap contributors."
)

UK_LAD_2024 = BoundarySet(
    path=ASSET_DIR / "uk_lad_2024.geojson",
    code_field="LAD24CD",
    name_field="LAD24NM",
    attribution=LAD_ATTRIBUTION,
    source=(
        "https://github.com/openactive-contrib/openactive-monitor/blob/main/"
        "volume-1/data-analysis/000-location-districts.geojson"
    ),
)


@functools.cache
def uk_local_authority_districts() -> Boundaries:
    """Every UK local authority district in WGS84, read and converted once per process."""
    return load_boundaries(UK_LAD_2024)
