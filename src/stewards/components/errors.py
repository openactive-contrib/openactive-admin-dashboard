"""One distinct message per API failure type. Never a silent empty table."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from stewards.api.errors import (
    ApiContractError,
    ApiError,
    ApiNotFound,
    ApiUnauthorized,
    ApiUnavailable,
)

_MESSAGES: tuple[tuple[type[ApiError], str, str], ...] = (
    (
        ApiUnauthorized,
        "The monitoring API rejected this deployment's credentials.",
        "Your Google sign-in is fine — the server-side API token needs renewing. "
        "Tell the tech team; no data can be shown until it is.",
    ),
    (
        ApiUnavailable,
        "The monitoring API is unavailable.",
        "The daily snapshot cannot be read right now. Nothing is lost — retry shortly.",
    ),
    (
        ApiNotFound,
        "The monitoring API has no data for this monitor yet.",
        "This monitor is registered in the dashboard but its endpoint is not live.",
    ),
    (
        ApiContractError,
        "The monitoring API returned an unexpected shape.",
        "This is a contract mismatch between the dashboard and the API, not a data problem. "
        "The detail is in the app logs.",
    ),
)


def render_api_error(exc: ApiError) -> None:
    """Render the message matching the failure type, most specific first."""
    for kind, headline, detail in _MESSAGES:
        if isinstance(exc, kind):
            st.error(f"**{headline}**\n\n{detail}")
            st.caption(f"Reported by the API client: {exc}")
            return
    st.error(f"**The monitoring API could not be read.**\n\n{exc}")


def guarded[T](fetch: Callable[[], T]) -> T | None:
    """Run a repository read, rendering the typed error and returning None on failure."""
    try:
        return fetch()
    except ApiError as exc:
        render_api_error(exc)
        return None
