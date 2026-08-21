"""Card surfaces.

Streamlit exposes no theme token for the background of `st.container(border=True)` — it
renders transparent — so on the brand canvas tint every card would flatten into an outline.
This module is the one place the app writes CSS, and it writes only what the theme cannot:
the white fill, radius and lift of a card.

Containers opt in by passing `key=card_key(...)`. Streamlit turns a container key into a
stable `st-key-<key>` class, which is why the rule below does not depend on Streamlit's
generated emotion class names.
"""

from __future__ import annotations

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from stewards.components import theme

CARD_PREFIX = "oacard"

_STYLES = f"""
<style>
  /* Card surfaces: white fill on the canvas tint. */
  div[class*="st-key-{CARD_PREFIX}"] {{
      background-color: {theme.SURFACE};
      border-radius: 10px;
      box-shadow: 0 1px 2px rgba(16, 32, 43, 0.04);
  }}
  /* Sparklines and KPI values should not carry the element toolbar on hover. */
  div[class*="st-key-{CARD_PREFIX}"] [data-testid="stElementToolbar"] {{
      display: none;
  }}
</style>
"""


def card_key(name: str) -> str:
    """Container key that opts a bordered container into the card surface."""
    return f"{CARD_PREFIX}_{name}"


def inject_card_styles() -> None:
    """Emit the card stylesheet once per script run, from `app.py`."""
    st.markdown(_STYLES, unsafe_allow_html=True)


def card(name: str) -> DeltaGenerator:
    """A bordered white card."""
    return st.container(border=True, key=card_key(name))
