"""Card surfaces and the type scale.

Streamlit exposes no theme token for the background of `st.container(border=True)` — it
renders transparent — so on the brand canvas tint every card would flatten into an outline.
Nor can theme config set a per-element type scale. This module is the one place the app
writes CSS, and it writes only what the theme cannot: card fill, and the sizes and weights
of the header bar and the KPI blocks.

Elements opt in through container keys, which Streamlit turns into stable `st-key-<key>`
classes — so nothing here depends on Streamlit's generated emotion class names, and no data
is ever interpolated into markup.
"""

from __future__ import annotations

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from stewards.components import theme

CARD_PREFIX = "oacard"

_STYLES = f"""
<style>
  /* --- card surfaces: white fill on the canvas tint ---------------------------------- */
  div[class*="st-key-{CARD_PREFIX}"] {{
      background-color: {theme.SURFACE};
      border-radius: 10px;
      box-shadow: 0 1px 2px rgba(16, 32, 43, 0.04);
  }}
  div[class*="st-key-{CARD_PREFIX}"] [data-testid="stElementToolbar"] {{
      display: none;
  }}

  /* --- header bar -------------------------------------------------------------------- */
  div[class*="st-key-{CARD_PREFIX}_header"] {{
      padding: 0.6rem 1.15rem;
      border-radius: 8px;
  }}
  div[class*="st-key-oacrumb"] p {{
      margin: 0;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.085em;
      color: {theme.LABEL};
  }}
  div[class*="st-key-oatitle"] p {{
      margin: 0.1rem 0 0;
      font-size: 1.22rem;
      font-weight: 600;
      letter-spacing: -0.012em;
      line-height: 1.25;
      color: {theme.INK};
  }}
  div[class*="st-key-oasnapshot"] p {{
      margin: 0;
      text-align: right;
      font-size: 0.76rem;
      line-height: 1.45;
      color: {theme.MUTED};
  }}
  div[class*="st-key-oasnapshot"] code {{
      background: transparent;
      padding: 0;
      font-size: 0.76rem;
      color: {theme.MUTED};
  }}
  div[class*="st-key-oasnapshotsource"] p {{
      color: {theme.LABEL};
  }}

  /* --- KPI blocks -------------------------------------------------------------------- */
  div[class*="st-key-oakpilabel"] p {{
      margin: 0;
      font-size: 0.69rem;
      font-weight: 600;
      letter-spacing: 0.055em;
      color: {theme.LABEL};
  }}
  div[class*="st-key-oakpirow"] {{
      gap: 0.4rem;
  }}
  /* KPI cards stack label / value / sub tightly. */
  div[class*="st-key-{CARD_PREFIX}_kpi"] > div > div[data-testid="stVerticalBlock"] {{
      gap: 0.15rem;
  }}
  div[class*="st-key-oakpivalue"] p {{
      margin: 0;
      font-size: 1.95rem;
      font-weight: 600;
      letter-spacing: -0.025em;
      line-height: 1.12;
  }}
  div[class*="st-key-oakpidelta"] p {{
      margin: 0 0 0.22rem;
      font-size: 0.78rem;
      color: {theme.MUTED};
  }}
  div[class*="st-key-oakpisub"] p {{
      margin: 0.3rem 0 0;
      font-size: 0.78rem;
      color: {theme.LABEL};
  }}

  /* Bring the header bar close to the top of the canvas. */
  [data-testid="stMainBlockContainer"] {{
      padding-top: 2rem;
  }}
</style>
"""


def card_key(name: str) -> str:
    """Container key that opts a bordered container into the card surface."""
    return f"{CARD_PREFIX}_{name}"


def inject_card_styles() -> None:
    """Emit the stylesheet once per script run, from `app.py`."""
    st.markdown(_STYLES, unsafe_allow_html=True)


def card(name: str) -> DeltaGenerator:
    """A bordered white card."""
    return st.container(border=True, key=card_key(name))
