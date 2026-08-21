"""Semantic colours are defined once and mapped consistently."""

from __future__ import annotations

import pytest

from stewards.components import theme
from stewards.monitors.thresholds import Tone

FIXED = {
    Tone.RED: "#C6413B",
    Tone.AMBER: "#C77F1A",
    Tone.GREEN: "#1F7A4C",
    Tone.GREY: "#5C6B76",
}


@pytest.mark.parametrize(("tone", "hex_value"), FIXED.items())
def test_semantic_colours_are_the_ones_the_brief_fixes(tone: Tone, hex_value: str) -> None:
    assert theme.FOREGROUND[tone] == hex_value


def test_primary_is_teal() -> None:
    assert theme.TEAL == "#0E8F8A"


def test_every_tone_has_a_background_and_a_markdown_colour() -> None:
    for tone in Tone:
        assert theme.BACKGROUND[tone].startswith("#")
        assert theme.markdown_colour(tone)


def test_cell_css_pairs_background_with_foreground() -> None:
    css = theme.cell_css("red")
    assert theme.RED_BG in css
    assert theme.RED in css


def test_cell_css_of_an_unknown_or_empty_tone_is_unstyled() -> None:
    assert theme.cell_css("") == ""
    assert theme.cell_css("purple") == ""
