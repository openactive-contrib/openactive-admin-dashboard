"""The loading card's key, and the repository reads that hand their spinner to it."""

from __future__ import annotations

import inspect

from stewards.api import repository
from stewards.components.loading import LOADING_KEY, loading_key


def test_the_key_is_prefixed_and_derived_from_the_message() -> None:
    key = loading_key("Loading the latest snapshot")
    assert key == f"{LOADING_KEY}_loading_the_latest_snapshot"


def test_distinct_messages_get_distinct_keys() -> None:
    """The app shell and a page each open a card in one run; one key twice is an error."""
    assert loading_key("Loading the latest snapshot") != loading_key("Loading monitor health")


def test_punctuation_never_reaches_the_key() -> None:
    assert loading_key("Loading site-venue pairs…") == f"{LOADING_KEY}_loading_site_venue_pairs"
    assert loading_key("") == f"{LOADING_KEY}_"


def test_every_cached_read_is_silent() -> None:
    """The cache's own spinner renders at the top of the page; `loading` replaces it."""
    source = inspect.getsource(repository)
    decorators = [line for line in source.splitlines() if "@st.cache_data(" in line]
    assert decorators
    assert all("show_spinner=False" in line for line in decorators)
