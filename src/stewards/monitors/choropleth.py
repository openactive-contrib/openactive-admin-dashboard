"""A choropleth: areas shaded by one figure, on a slippy map. No Streamlit, no I/O.

The app's one map builder, for any view that has a figure per area. A caller hands it the
boundaries (`stewards.geography`, always WGS84), its rows keyed by area code, the field to
shade by and the fields to show on hover. It returns a pydeck `Deck` for `st.pydeck_chart`
and a small Altair legend for it; `components.map_chart.render_map` puts both on a card.

Why deck.gl rather than Altair, which draws every other chart here: a map is explored, not
read at a glance. Stewards zoom from England into a London borough and back, and the gestures
everyone already knows from web maps are the interface — the wheel or a pinch zooms at the
cursor, a drag pans, a double-click zooms in. Vega-Lite binds those gestures to x/y scales
only, and a map has none. deck.gl gives them natively, inside Streamlit's own element, with
no custom component. North stays up: rotation and tilt are switched off, because a rotated
choropleth answers no question a steward has and is easy to get into by accident.

Hover, not click, shows an area's figures: the tooltip is drawn in the browser and costs
nothing, while a click selection reruns the whole script. The hovered area darkens, so the
tooltip is never ambiguous about which area it describes.

The features reach the browser as a compact `data:` URL that deck.gl's loader fetches like
any other. Inline, they would ride in the spec `st.pydeck_chart` sends, which pydeck
serialises with two-space indentation — every coordinate on a line of its own, four times the
bytes.

Every figure is shaded and formatted here, in Python, so the browser receives a colour and a
finished line of text per area — the tooltip is plain text, never markup, and what it says is
unit-tested. Pure, as `trend.py` is: the caller passes the colours.
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import altair as alt
import pandas as pd
import pydeck as pdk

from stewards.geography import CODE, NAME, Boundaries, Extent

#: What the hover reads for a figure the rows do not report.
NOT_REPORTED = "Not reported"

#: The finished tooltip text and the fill colour each feature carries to the browser.
TOOLTIP = "tooltip"
FILL = "fill"

#: Zoom limits: a little wider than Great Britain, and in to street level around a venue.
MIN_ZOOM = 4.0
MAX_ZOOM = 13.0

#: deck.gl's world is 512 px wide at zoom 0.
WORLD_PX = 512.0

#: The width the initial view is fitted to. The card's real width is the browser's to know;
#: this is a typical one, and the user zooms from there.
ASSUMED_WIDTH_PX = 900.0

#: Share of the view the fitted extent may fill, leaving a margin around it.
FIT_PADDING = 0.9

#: Centring on an area fits a box this many times its size, so its neighbours stay in sight.
FOCUS_CONTEXT = 3.0

#: Outline width in screen pixels, at every zoom: the width proper is zero metres, and the
#: minimum in pixels is what draws.
STROKE_PX = 0.6

DATA_URL_PREFIX = "data:application/json;base64,"

#: Alpha of the hovered area's overlay, 0-255.
HIGHLIGHT_ALPHA = 90

LEGEND_WIDTH = 280
LEGEND_STEPS = 64
#: Title above, bar, tick values below, in pixels from the legend's top.
LEGEND_BAR_TOP = 18
LEGEND_BAR_HEIGHT = 10
LEGEND_HEIGHT = 46


@dataclass(frozen=True, slots=True)
class TooltipField:
    """One line of the hover. `field` is a key of the caller's rows. A field with a `format`
    is numeric, formatted with that Python format spec and then `suffix`."""

    field: str
    title: str
    format: str | None = None
    suffix: str = ""


@dataclass(frozen=True, slots=True)
class ChoroplethStyle:
    """Colours and size, from the caller. `ramp` is a sequential ramp, light to dark."""

    ramp: Sequence[str]
    no_data: str
    stroke: str
    highlight: str
    label_colour: str
    tooltip_background: str
    tooltip_text: str
    tooltip_border: str
    legend_title: str
    height: int = 560
    #: Fill opacity, so the basemap's coastline and roads read through when zoomed in.
    opacity: float = 0.85


@dataclass(frozen=True, slots=True)
class Joined:
    """Boundaries with the rows' values merged into their properties.

    `unmatched` lists the codes of rows no boundary carries — usually a boundary set from a
    different year than the data's codes, which a caller should say rather than hide.
    """

    features: tuple[dict[str, Any], ...]
    unmatched: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MapView:
    latitude: float
    longitude: float
    zoom: float


@dataclass(frozen=True, slots=True)
class Choropleth:
    """The map and its legend, which deck.gl does not draw."""

    deck: pdk.Deck
    legend: alt.LayerChart


# --- joining ------------------------------------------------------------------------------


def join(boundaries: Boundaries, rows: Iterable[Mapping[str, Any]], *, code_key: str) -> Joined:
    """Each row's values into the properties of the area with its code.

    An area with no row keeps only its code and name, and draws as no data. A row value
    overrides the boundary's own `name`, so a row that carries a name is shown under it.
    """
    by_code: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        code = str(row.get(code_key) or "").strip()
        if code:
            by_code[code] = row
    features = []
    for feature in boundaries.features:
        properties = dict(feature["properties"])
        match = by_code.get(properties[CODE])
        if match is not None:
            properties.update({key: value for key, value in match.items() if key != code_key})
        features.append(
            {"type": "Feature", "geometry": feature["geometry"], "properties": properties}
        )
    known = set(boundaries.codes)
    return Joined(tuple(features), tuple(sorted(code for code in by_code if code not in known)))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def data_url(document: Mapping[str, Any]) -> str:
    """A JSON document as a `data:` URL, compactly encoded."""
    payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return DATA_URL_PREFIX + base64.b64encode(payload).decode("ascii")


def read_data_url(url: str) -> Any:
    """The JSON document inside a `data_url` — for tests, and for anyone inspecting a spec."""
    if not url.startswith(DATA_URL_PREFIX):
        raise ValueError("not a JSON data URL")
    return json.loads(base64.b64decode(url.removeprefix(DATA_URL_PREFIX)))


# --- colour -------------------------------------------------------------------------------


def value_domain(values: Iterable[float | None], *, step: float = 10.0) -> tuple[float, float]:
    """Zero to the largest value rounded up to `step`.

    From the data rather than a fixed 0-100: when every area sits below 50, a 0-100 scale
    spends half the ramp on values nothing has, and the areas blur together.
    """
    top = max((value for value in values if value is not None), default=0.0)
    if top <= 0:
        return (0.0, step)
    return (0.0, math.ceil(top / step) * step)


def rgb(hex_colour: str) -> list[int]:
    """A six-digit hex colour, with or without its hash, as [red, green, blue]."""
    value = hex_colour.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected a six-digit hex colour, got {hex_colour!r}")
    return [int(value[i : i + 2], 16) for i in (0, 2, 4)]


def ramp_colour(value: float, domain: tuple[float, float], ramp: Sequence[str]) -> list[int]:
    """The ramp's colour at `value`, interpolated between its evenly spaced stops; a value
    outside the domain takes the end it passed."""
    stops = [rgb(colour) for colour in ramp]
    if len(stops) == 1:
        return stops[0]
    low, high = domain
    share = 0.0 if high <= low else min(max((value - low) / (high - low), 0.0), 1.0)
    position = share * (len(stops) - 1)
    index = min(int(position), len(stops) - 2)
    t = position - index
    start, end = stops[index], stops[index + 1]
    return [round(a + (b - a) * t) for a, b in zip(start, end, strict=True)]


# --- tooltip ------------------------------------------------------------------------------


def format_value(value: Any, item: TooltipField) -> str:
    if value is None or isinstance(value, bool):
        return NOT_REPORTED
    if item.format is None:
        return str(value)
    if not isinstance(value, int | float):
        return NOT_REPORTED
    return f"{value:{item.format}}{item.suffix}"


def tooltip_text(properties: Mapping[str, Any], fields: Sequence[TooltipField]) -> str:
    """The area's name, then one `Title: value` line per field."""
    lines = [str(properties.get(NAME) or properties.get(CODE) or "")]
    lines.extend(
        f"{item.title}: {format_value(properties.get(item.field), item)}" for item in fields
    )
    return "\n".join(lines)


# --- view ---------------------------------------------------------------------------------


def _mercator_y(latitude: float) -> float:
    """Latitude -> Web Mercator y, 0 at the top of the world and 1 at the bottom."""
    lat = math.radians(max(min(latitude, 85.0), -85.0))
    return (1 - math.log(math.tan(lat) + 1 / math.cos(lat)) / math.pi) / 2


def _latitude(mercator_y: float) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * mercator_y))))


def fit_view(
    extent: Extent, *, height: float, width: float = ASSUMED_WIDTH_PX, scale: float = 1.0
) -> MapView:
    """The centre and zoom that fit `extent`, grown `scale` times about its centre, into a
    `width` by `height` view. A point fits at the maximum zoom."""
    west, south, east, north = extent
    top, bottom = _mercator_y(north), _mercator_y(south)
    span_x = (east - west) / 360 * scale
    span_y = (bottom - top) * scale
    fits = [
        size * FIT_PADDING / (WORLD_PX * span)
        for size, span in ((width, span_x), (height, span_y))
        if span > 0
    ]
    zoom = math.log2(min(fits)) if fits else MAX_ZOOM
    return MapView(
        latitude=_latitude((top + bottom) / 2),
        longitude=(west + east) / 2,
        zoom=min(max(zoom, MIN_ZOOM), MAX_ZOOM),
    )


def view_for(boundaries: Boundaries, focus: str | None, *, height: float) -> MapView:
    """The whole set, or `focus` with its neighbours around it — never wider than the whole.

    An unknown focus is the whole set.
    """
    whole = boundaries.extent()
    if whole is None:
        return MapView(0.0, 0.0, MIN_ZOOM)
    overview = fit_view(whole, height=height)
    area = boundaries.extent([focus]) if focus else None
    if area is None:
        return overview
    close = fit_view(area, height=height, scale=FOCUS_CONTEXT)
    return MapView(close.latitude, close.longitude, max(close.zoom, overview.zoom))


# --- the map ------------------------------------------------------------------------------


def choropleth(
    boundaries: Boundaries,
    rows: Iterable[Mapping[str, Any]],
    *,
    code_key: str,
    value_field: str,
    tooltip: Sequence[TooltipField],
    style: ChoroplethStyle,
    focus: str | None = None,
    domain: tuple[float, float] | None = None,
) -> Choropleth | None:
    """Every area in `boundaries`, shaded by `value_field` where a row reports it.

    An area with no figure is the no-data grey, never the ramp's low end: no figure is not a
    zero one. None when no row reports the figure at all, matching every other builder, so
    the caller draws a caption instead of a grey country. `focus` is the area the view opens
    on; the user zooms and pans from there.
    """
    joined = join(boundaries, rows, code_key=code_key)
    values = [_number(feature["properties"].get(value_field)) for feature in joined.features]
    if all(value is None for value in values):
        return None
    scale = domain or value_domain(values)
    no_data = rgb(style.no_data)

    features = [
        {
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": {
                CODE: feature["properties"][CODE],
                NAME: feature["properties"].get(NAME, ""),
                FILL: no_data if value is None else ramp_colour(value, scale, style.ramp),
                TOOLTIP: tooltip_text(feature["properties"], tooltip),
            },
        }
        for feature, value in zip(joined.features, values, strict=True)
    ]
    view = view_for(boundaries, focus, height=style.height)
    layer = pdk.Layer(
        "GeoJsonLayer",
        data=data_url({"type": "FeatureCollection", "features": features}),
        id="areas",
        pickable=True,
        stroked=True,
        filled=True,
        opacity=style.opacity,
        get_fill_color=f"properties.{FILL}",
        get_line_color=rgb(style.stroke),
        # Not `line_width_units="pixels"`: pydeck turns every string argument into an
        # accessor expression, and "@@=pixels" is not a unit.
        get_line_width=0,
        line_width_min_pixels=STROKE_PX,
        auto_highlight=True,
        highlight_color=[*rgb(style.highlight), HIGHLIGHT_ALPHA],
    )
    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=pdk.ViewState(
            latitude=view.latitude,
            longitude=view.longitude,
            zoom=view.zoom,
            min_zoom=MIN_ZOOM,
            max_zoom=MAX_ZOOM,
            pitch=0,
            bearing=0,
        ),
        views=[
            pdk.View(
                type="MapView",
                controller={
                    "scrollZoom": True,
                    "dragPan": True,
                    "doubleClickZoom": True,
                    "touchZoom": True,
                    "keyboard": True,
                    "dragRotate": False,
                    "touchRotate": False,
                },
            )
        ],
        map_provider="carto",
        map_style="light",
        tooltip={
            "text": "{" + TOOLTIP + "}",
            "style": {
                "backgroundColor": style.tooltip_background,
                "color": style.tooltip_text,
                "border": f"1px solid {style.tooltip_border}",
                "borderRadius": "6px",
                "padding": "8px 10px",
                "fontSize": "13px",
                "lineHeight": "1.5",
            },
        },
    )
    return Choropleth(deck=deck, legend=legend_chart(scale, style))


def legend_ticks(domain: tuple[float, float], *, most: int = 7) -> list[float]:
    """Round tick values across `domain`, at the smallest 1-2-5 step that cuts it into at
    most `most` intervals."""
    low, high = domain
    span = high - low
    if span <= 0:
        return [low]
    magnitude = 10 ** math.floor(math.log10(span / most))
    for factor in (1, 2, 5, 10):
        step = factor * magnitude
        if span / step <= most:
            break
    first = math.ceil(low / step) * step
    count = math.floor((high - first) / step + 1e-9) + 1
    return [round(first + i * step, 10) for i in range(count)]


def _tick_label(value: float) -> str:
    return f"{value:g}"


def legend_chart(domain: tuple[float, float], style: ChoroplethStyle) -> alt.LayerChart:
    """The ramp as a thin gradient bar, its title above and its tick values below.

    Every part sits at a fixed pixel position, with no Vega axis: `st.altair_chart` fits a
    chart *including* its axes into the declared size, which squeezes an axis-bearing bar to
    nothing. Each segment runs two steps long, under the next one, so no hairline of
    background shows between neighbours where antialiasing would otherwise leave one.
    """
    low, high = domain
    step = (high - low) / LEGEND_STEPS
    bar = pd.DataFrame(
        {
            "start": [low + i * step for i in range(LEGEND_STEPS)],
            "end": [min(low + (i + 2) * step, high) for i in range(LEGEND_STEPS)],
        }
    )
    ticks = pd.DataFrame(
        {"value": (values := legend_ticks(domain)), "label": [_tick_label(v) for v in values]}
    )
    x_scale = alt.Scale(domain=[low, high], nice=False, zero=False)

    title = (
        alt.Chart(pd.DataFrame({"title": [style.legend_title]}))
        .mark_text(align="left", baseline="top", fontSize=12, color=style.label_colour)
        .encode(x=alt.value(0), y=alt.value(0), text="title:N")
    )
    gradient = (
        alt.Chart(bar)
        .mark_rect()
        .encode(
            x=alt.X("start:Q", scale=x_scale, axis=None),
            x2="end:Q",
            y=alt.value(LEGEND_BAR_TOP),
            y2=alt.value(LEGEND_BAR_TOP + LEGEND_BAR_HEIGHT),
            color=alt.Color(
                "start:Q",
                scale=alt.Scale(domain=[low, high], range=list(style.ramp)),
                legend=None,
            ),
        )
    )
    labels = (
        alt.Chart(ticks)
        .mark_text(baseline="top", fontSize=11, color=style.label_colour)
        .encode(
            x=alt.X("value:Q", scale=x_scale, axis=None),
            y=alt.value(LEGEND_BAR_TOP + LEGEND_BAR_HEIGHT + 4),
            text="label:N",
        )
    )
    chart: alt.LayerChart = (
        alt.layer(title, gradient, labels)
        .properties(
            width=LEGEND_WIDTH,
            height=LEGEND_HEIGHT,
            padding={"left": 8, "right": 12, "top": 0, "bottom": 0},
        )
        .configure_view(strokeWidth=0, fill=None)
        .configure(background="transparent")
    )
    return chart
