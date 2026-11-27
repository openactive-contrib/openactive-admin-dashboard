"""Typed API failures. Pages render a distinct message per type."""

from __future__ import annotations

from typing import Any


class ApiError(RuntimeError):
    """Base class for every stewards API failure.

    `detail` is the API's own error body, where the contract says it sends one: a refused
    capture names the measurements it refused, and the monthly job has to tell a batch that
    was already captured from one that only partly clashed. It is None otherwise, and it is
    never rendered on a page — a page shows the message.
    """

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class ApiUnavailable(ApiError):
    """The API could not be reached, timed out, or returned 5xx."""


class ApiUnauthorized(ApiError):
    """The API rejected our token (401/403)."""


class ApiNotFound(ApiError):
    """The requested resource does not exist (404)."""


class ApiConflict(ApiError):
    """The write clashes with what is already stored (409), e.g. a POST of an existing id."""


class ApiContractError(ApiError):
    """The API responded, but not in the shape this app models."""
