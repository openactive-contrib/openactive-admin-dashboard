"""The only place a colour is written down.

The palette is grouped exactly as the brand defines it. Streamlit renders almost all of it
from `.streamlit/config.toml`; the constants here are for the few places the app must name a
colour itself (the RAG table shading and the trend chart series). `tests/unit/test_theme.py`
asserts the two files agree, so the config and this module cannot drift apart.
"""

from __future__ import annotations

from typing import Literal

from stewards.monitors.thresholds import Tone

#: The subset of Streamlit's semantic colour names this app uses. Matches the slots that
#: `.streamlit/config.toml` repoints at the brand palette.
SemanticColour = Literal["red", "orange", "green", "gray"]

# --- brand -------------------------------------------------------------------------------

TEAL = "#0E8F8A"
"""Primary: actions, links, chart series."""

TEAL_HOVER = "#0B6F6B"
TEAL_TINT = "#EAF3F3"

# --- neutrals ----------------------------------------------------------------------------

INK = "#16202A"
"""Body text."""

INK_SOFT = "#2B3A45"
INK_SOFTER = "#4A5A65"
MUTED = "#5C6B76"
"""Secondary text; doubles as the grey RAG tone."""

LABEL = "#8A98A2"
"""Uppercase KPI labels and metadata."""

BORDER = "#E2E6E9"
BORDER_SUBTLE = "#EDF0F2"

SURFACE = "#FFFFFF"
"""Cards and the main content area."""

SURFACE_RAISED = "#F7F9F9"
"""Table headers and footers."""

SURFACE_SUNKEN = "#F1F4F5"
"""Widget fills, code blocks; doubles as the grey RAG tint."""

CANVAS = "#EEF1F3"
"""The page canvas behind the cards. Streamlit has no token for a container's fill, so
`components/surface.py` supplies the white card surface that sits on this tint."""

# --- sidebar (dark) ----------------------------------------------------------------------

SIDEBAR_BG = "#10202B"
SIDEBAR_ACTIVE = "#1B333F"
SIDEBAR_FIELD = "#182F3C"
SIDEBAR_RULE = "#24404F"
SIDEBAR_TEXT = "#C8D4DB"
SIDEBAR_DIM = "#5E7480"

# --- RAG pairs ---------------------------------------------------------------------------

RED = "#C6413B"
"""Critical, past threshold."""

AMBER = "#C77F1A"
"""Warning, monitoring."""

GREEN = "#1F7A4C"
"""Healthy, on target."""

GREY = MUTED
"""New, informational."""

RED_BG = "#FBEDEC"
AMBER_BG = "#FDF3E3"
GREEN_BG = "#EAF4EE"
GREY_BG = SURFACE_SUNKEN

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


#: Streamlit's semantic markdown colours, so no page needs inline HTML. The config file
#: points each of these at the brand RAG pair, so `:red[…]` renders `RED` on `RED_BG`.
MARKDOWN_COLOUR: dict[Tone, SemanticColour] = {
    Tone.RED: "red",
    Tone.AMBER: "orange",
    Tone.GREEN: "green",
    Tone.GREY: "gray",
}


def markdown_colour(tone: Tone) -> SemanticColour:
    """Streamlit colour name for a tone, usable in markdown directives and `st.badge`."""
    return MARKDOWN_COLOUR[tone]
