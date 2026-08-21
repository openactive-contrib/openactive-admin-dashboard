"""The palette.

Two jobs: pin the brand values so a colour cannot be changed by accident, and assert
`.streamlit/config.toml` still agrees with `components/theme.py` so the config and the module
cannot drift apart.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from stewards.components import theme
from stewards.monitors.thresholds import Tone

CONFIG = Path(__file__).resolve().parents[2] / ".streamlit" / "config.toml"

BRAND = {
    "TEAL": "#0E8F8A",
    "TEAL_HOVER": "#0B6F6B",
    "TEAL_TINT": "#EAF3F3",
}

NEUTRALS = {
    "INK": "#16202A",
    "INK_SOFT": "#2B3A45",
    "INK_SOFTER": "#4A5A65",
    "MUTED": "#5C6B76",
    "LABEL": "#8A98A2",
    "BORDER": "#E2E6E9",
    "BORDER_SUBTLE": "#EDF0F2",
    "SURFACE": "#FFFFFF",
    "SURFACE_RAISED": "#F7F9F9",
    "SURFACE_SUNKEN": "#F1F4F5",
    "CANVAS": "#EEF1F3",
}

SIDEBAR = {
    "SIDEBAR_BG": "#10202B",
    "SIDEBAR_ACTIVE": "#1B333F",
    "SIDEBAR_FIELD": "#182F3C",
    "SIDEBAR_RULE": "#24404F",
    "SIDEBAR_TEXT": "#C8D4DB",
    "SIDEBAR_DIM": "#5E7480",
}

RAG_TEXT = {
    Tone.RED: "#C6413B",
    Tone.AMBER: "#C77F1A",
    Tone.GREEN: "#1F7A4C",
    Tone.GREY: "#5C6B76",
}

RAG_TINT = {
    Tone.RED: "#FBEDEC",
    Tone.AMBER: "#FDF3E3",
    Tone.GREEN: "#EAF4EE",
    Tone.GREY: "#F1F4F5",
}


@pytest.fixture(scope="module")
def config() -> dict[str, dict[str, object]]:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


# --- the palette itself -------------------------------------------------------------------


@pytest.mark.parametrize(("name", "value"), (BRAND | NEUTRALS | SIDEBAR).items())
def test_palette_values_are_the_brand_values(name: str, value: str) -> None:
    assert getattr(theme, name) == value


@pytest.mark.parametrize(("tone", "value"), RAG_TEXT.items())
def test_rag_text_colours(tone: Tone, value: str) -> None:
    assert theme.FOREGROUND[tone] == value


@pytest.mark.parametrize(("tone", "value"), RAG_TINT.items())
def test_rag_tint_colours(tone: Tone, value: str) -> None:
    assert theme.BACKGROUND[tone] == value


def test_the_grey_tone_reuses_the_muted_neutral() -> None:
    """The brand lists one value for both; they must not become two constants that drift."""
    assert theme.GREY is theme.MUTED
    assert theme.GREY_BG is theme.SURFACE_SUNKEN


def test_every_tone_has_a_text_colour_a_tint_and_a_markdown_colour() -> None:
    for tone in Tone:
        assert theme.FOREGROUND[tone].startswith("#")
        assert theme.BACKGROUND[tone].startswith("#")
        assert theme.markdown_colour(tone)


def test_all_palette_values_are_uppercase_six_digit_hex() -> None:
    names = BRAND | NEUTRALS | SIDEBAR | {f"tone_{t}": v for t, v in RAG_TEXT.items()}
    for name in names:
        value = getattr(theme, name, None) or names[name]
        assert len(value) == 7
        assert value.startswith("#")
        assert value == value.upper()


# --- cell shading -------------------------------------------------------------------------


@pytest.mark.parametrize("tone", list(Tone))
def test_cell_css_pairs_the_tint_with_the_text_colour(tone: Tone) -> None:
    css = theme.cell_css(tone.value)
    assert f"background-color: {RAG_TINT[tone]}" in css
    assert f"color: {RAG_TEXT[tone]}" in css


def test_cell_css_of_an_unknown_or_empty_tone_is_unstyled() -> None:
    assert theme.cell_css("") == ""
    assert theme.cell_css("purple") == ""


# --- the Streamlit config -----------------------------------------------------------------


def test_config_brand_and_neutrals_come_from_the_palette(config: dict) -> None:
    main = config["theme"]
    assert main["primaryColor"] == theme.TEAL
    assert main["linkColor"] == theme.TEAL
    assert main["backgroundColor"] == theme.CANVAS  # cards get SURFACE from surface.py
    assert main["secondaryBackgroundColor"] == theme.SURFACE_SUNKEN
    assert main["textColor"] == theme.INK
    assert main["borderColor"] == theme.BORDER
    assert main["dataframeBorderColor"] == theme.BORDER_SUBTLE
    assert main["dataframeHeaderBackgroundColor"] == theme.SURFACE_RAISED
    assert main["codeBackgroundColor"] == theme.SURFACE_SUNKEN
    assert main["codeTextColor"] == theme.INK_SOFT


@pytest.mark.parametrize(
    ("slot", "tone"),
    [
        ("red", Tone.RED),
        ("orange", Tone.AMBER),
        ("yellow", Tone.AMBER),
        ("green", Tone.GREEN),
        ("gray", Tone.GREY),
    ],
)
def test_config_semantic_slots_carry_the_rag_pairs(config: dict, slot: str, tone: Tone) -> None:
    """st.error/st.warning/st.success and `:red[…]` all resolve through these."""
    main = config["theme"]
    assert main[f"{slot}Color"] == theme.FOREGROUND[tone]
    assert main[f"{slot}BackgroundColor"] == theme.BACKGROUND[tone]
    assert main[f"{slot}TextColor"] == theme.FOREGROUND[tone]


def test_config_maps_blue_onto_teal_so_st_info_stays_on_brand(config: dict) -> None:
    main = config["theme"]
    assert main["blueColor"] == theme.TEAL
    assert main["blueBackgroundColor"] == theme.TEAL_TINT
    assert main["blueTextColor"] == theme.TEAL_HOVER


def test_config_chart_colours_are_the_teal_series_then_the_rag_tones(config: dict) -> None:
    assert config["theme"]["chartCategoricalColors"] == [
        theme.TEAL,
        theme.RED,
        theme.AMBER,
        theme.GREEN,
        theme.GREY,
    ]


def test_config_sidebar_is_the_dark_palette(config: dict) -> None:
    sidebar = config["theme"]["sidebar"]
    assert sidebar["backgroundColor"] == theme.SIDEBAR_BG
    assert sidebar["secondaryBackgroundColor"] == theme.SIDEBAR_FIELD
    assert sidebar["textColor"] == theme.SIDEBAR_TEXT
    assert sidebar["borderColor"] == theme.SIDEBAR_RULE
    assert sidebar["grayColor"] == theme.SIDEBAR_DIM
    assert sidebar["codeBackgroundColor"] == theme.SIDEBAR_ACTIVE
    assert sidebar["primaryColor"] == theme.TEAL


def test_config_declares_no_colour_outside_the_palette(config: dict) -> None:
    """Any hex in config.toml must be a value theme.py defines."""
    known = {
        value
        for name, value in vars(theme).items()
        if name.isupper() and isinstance(value, str) and value.startswith("#")
    }
    found: list[str] = []
    for section in (config["theme"], config["theme"]["sidebar"]):
        for value in section.values():
            if isinstance(value, str) and value.startswith("#"):
                found.append(value)
            elif isinstance(value, list):
                found.extend(v for v in value if isinstance(v, str) and v.startswith("#"))
    assert found
    assert set(found) <= known, (
        f"config.toml uses colours theme.py does not define: {set(found) - known}"
    )


def test_every_key_in_the_config_is_a_real_streamlit_option(config: dict) -> None:
    """Guards against a typo'd theme key silently doing nothing."""
    from streamlit import config as st_config

    st_config.get_config_options()
    known = set(st_config._config_options)
    for section, values in config.items():
        for key, value in values.items():
            if isinstance(value, dict):
                for nested in value:
                    assert f"{section}.{key}.{nested}" in known, f"{section}.{key}.{nested}"
            else:
                assert f"{section}.{key}" in known, f"{section}.{key}"


# --- the card surface ---------------------------------------------------------------------


def test_the_card_stylesheet_uses_only_palette_colours() -> None:
    """The one place the app writes CSS must still take its colours from the palette."""
    import re

    from stewards.components.surface import _STYLES

    hexes = set(re.findall(r"#[0-9A-Fa-f]{6}", _STYLES))
    known = {
        value
        for name, value in vars(theme).items()
        if name.isupper() and isinstance(value, str) and value.startswith("#")
    }
    assert hexes <= known, f"surface.py uses colours theme.py does not define: {hexes - known}"


def test_the_card_surface_is_the_white_surface_on_the_canvas() -> None:
    from stewards.components.surface import _STYLES

    assert f"background-color: {theme.SURFACE}" in _STYLES
    assert theme.SURFACE != theme.CANVAS


def test_card_keys_carry_the_prefix_the_stylesheet_targets() -> None:
    from stewards.components.surface import CARD_PREFIX, card_key

    assert card_key("tile_http_failure").startswith(CARD_PREFIX)
    assert CARD_PREFIX in _card_selector()


def _card_selector() -> str:
    from stewards.components.surface import _STYLES

    return _STYLES


def test_no_source_file_outside_theme_inlines_a_hex_colour() -> None:
    """Semantic colours are fixed in one place; a stray hex elsewhere would fork the palette."""
    import re

    src = Path(__file__).resolve().parents[2] / "src" / "stewards"
    offenders = {}
    for path in sorted(src.rglob("*.py")):
        if path.name in {"theme.py", "surface.py"}:
            continue  # the palette itself, and the one stylesheet that reads from it
        hexes = re.findall(r"#[0-9A-Fa-f]{6}\b", path.read_text(encoding="utf-8"))
        if hexes:
            offenders[path.name] = hexes
    assert not offenders, f"inline colours found: {offenders}"
