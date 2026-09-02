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


def test_the_login_note_is_the_sunken_tint_not_an_alert_colour() -> None:
    """Reassurance is not a state, so the note must not borrow st.info's teal."""
    assert 'div[class*="st-key-oaloginnote"]' in _STYLES
    assert f"background-color: {theme.SURFACE_SUNKEN}" in _STYLES


def test_the_login_card_carries_its_own_type_scale() -> None:
    """The sign-in screen's hierarchy is CSS only; losing a key silently flattens it."""
    for key in ("oalogineyebrow", "oalogintitle", "oaloginbody", "oaloginaction"):
        assert f'div[class*="st-key-{key}"]' in _STYLES
    assert f'div[class*="st-key-{CARD_PREFIX}_login"]' in _STYLES
