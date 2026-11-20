"""Contracts in the one stylesheet.

`surface.py` writes only what the theme cannot express, and two of those rules exist to
hold a layout the Python side depends on. A silent edit to either would leave the sidebar
looking wrong with every test still green, so they are asserted here.
"""

from __future__ import annotations

from stewards.components import theme
from stewards.components.surface import (
    _STYLES,
    CARD_PREFIX,
    SIDEBAR_FOOT_KEY,
)


def test_the_sidebar_logo_keeps_its_plaque() -> None:
    """Without it the navy wordmark sits at 1.56:1 on the sidebar — effectively invisible."""
    assert '[data-testid="stSidebarLogo"]' in _STYLES


def test_the_sidebar_footer_is_pinned_to_the_bottom() -> None:
    """All three rules are needed: margin-top:auto only pushes inside a full-height column."""
    assert '[data-testid="stSidebarUserContent"]' in _STYLES
    assert 'div[data-testid="stVerticalBlock"] {\n      flex: 1;' in _STYLES
    assert f'div[class*="st-key-{SIDEBAR_FOOT_KEY}"] {{\n      margin-top: auto;' in _STYLES


def test_kpi_cards_fill_their_column_so_a_row_is_level() -> None:
    """Both rules are needed. Streamlit stretches the columns to the tallest card already;
    the card's own height resolves against the wrapper above it, which sizes to its content
    until it is given a height too, so dropping either leaves the row ragged."""
    card = f'div[class*="st-key-{CARD_PREFIX}"][class*="kpi"]'
    wrapper = f'div[data-testid="stLayoutWrapper"]:has(> {card})'
    assert f"{wrapper} {{\n      height: 100%;" in _STYLES
    assert f"\n  {card} {{\n      height: 100%;" in _STYLES


def test_a_kpi_label_reserves_two_lines_so_the_values_line_up() -> None:
    """Equal card heights alone are not enough: without this the cards match while the
    numbers inside them step up and down as one label wraps and the next does not."""
    assert 'div[class*="st-key-oakpilabel"] {\n      min-height: 2.2rem;' in _STYLES


def test_every_kpi_card_matches_whichever_row_it_is_in() -> None:
    """The selectors key off `kpi` appearing anywhere in the card's key, so a tab's own
    figures are styled by the same rules as the page's. Matching `{CARD_PREFIX}_kpi` would
    silently miss them."""
    assert f'st-key-{CARD_PREFIX}_kpi"' not in _STYLES


def test_the_login_note_is_the_sunken_tint_not_an_alert_colour() -> None:
    """Reassurance is not a state, so the note must not borrow st.info's teal."""
    assert 'div[class*="st-key-oaloginnote"]' in _STYLES
    assert f"background-color: {theme.SURFACE_SUNKEN}" in _STYLES


def test_the_login_card_carries_its_own_type_scale() -> None:
    """The sign-in screen's hierarchy is CSS only; losing a key silently flattens it."""
    for key in ("oalogineyebrow", "oalogintitle", "oaloginbody", "oaloginaction"):
        assert f'div[class*="st-key-{key}"]' in _STYLES
    assert f'div[class*="st-key-{CARD_PREFIX}_login"]' in _STYLES


def test_the_loading_card_is_styled_by_its_container_key() -> None:
    """The card is the spinner inside the loader's container; without the rule it is the
    bare line at the top of the page again."""
    from stewards.components.loading import LOADING_KEY

    assert f'div[class*="st-key-{LOADING_KEY}"] [data-testid="stSpinner"]' in _STYLES
    assert f"background-color: {theme.SURFACE}" in _STYLES
