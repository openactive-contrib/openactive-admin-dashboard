"""The loading card shown while a page's reads run.

`st.cache_data`'s own spinner renders as a bare line at the very top of the page, wherever
the cached function happens to be called. Every repository read turns it off
(`show_spinner=False`) and the page wraps its reads in `loading` instead, which puts one
centred card in the page body and clears it once the reads return.

Streamlit's spinner only appears after half a second, so a read served from the cache shows
nothing at all rather than flashing the card on every rerun.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

import streamlit as st

#: Container key prefix the loading card's style hooks onto. See `components.surface`.
LOADING_KEY = "oaloading"


def loading_key(message: str) -> str:
    """A key per message: the app shell and the page each open a card in one run, and
    Streamlit rejects a key used twice even once its element has been emptied."""
    return f"{LOADING_KEY}_{re.sub(r'[^a-z0-9]+', '_', message.lower()).strip('_')}"


@contextmanager
def loading(message: str) -> Iterator[None]:
    """Show the loading card for the duration of the block.

    Messages must differ between the cards one run opens, because each names its key.
    Only reads belong inside the block: the card's slot is emptied on the way out, whether
    the reads returned or raised, and anything rendered inside would be emptied with it.
    """
    slot = st.empty()
    try:
        with slot.container(key=loading_key(message)), st.spinner(message):
            yield
    finally:
        slot.empty()
