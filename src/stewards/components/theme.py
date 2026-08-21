"""The only place a colour is written down.

Semantic colours are fixed by the brief: red critical / past threshold, amber warning /
monitoring, green healthy / on target, grey new / informational, teal primary.
"""

from __future__ import annotations

from stewards.monitors.thresholds import Tone

RED = "#C6413B"
AMBER = "#C77F1A"
GREEN = "#1F7A4C"
GREY = "#5C6B76"
TEAL = "#0E8F8A"

RED_BG = "#FBEDEC"
AMBER_BG = "#FDF3E3"
GREEN_BG = "#EAF4EE"
GREY_BG = "#F1F4F5"

INK = "#16202A"
MUTED = "#8A98A2"

FOREGROUND: dict[Tone, str] = {
    Tone.RED: RED,
    Tone.AMBER: AMBER,
    Tone.GREEN: GREEN,
    Tone.GREY: GREY,
}

BACKGROUND: dict[Tone, str] = {
    Tone.RED: RED_BG,
    Tone.AMBER: AMBER_BG,
    Tone.GREEN: GREEN_BG,
    Tone.GREY: GREY_BG,
}


def cell_css(tone: str) -> str:
    """CSS for one RAG-shaded table cell. An unknown or empty tone is left unstyled."""
    try:
        key = Tone(tone)
    except ValueError:
        return ""
    return f"background-color: {BACKGROUND[key]}; color: {FOREGROUND[key]};"


#: Streamlit's built-in markdown colour names, so no page needs inline HTML.
MARKDOWN_COLOUR: dict[Tone, str] = {
    Tone.RED: "red",
    Tone.AMBER: "orange",
    Tone.GREEN: "green",
    Tone.GREY: "gray",
}


def markdown_colour(tone: Tone) -> str:
    return MARKDOWN_COLOUR[tone]
