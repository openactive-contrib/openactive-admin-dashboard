"""Typed API failures. Pages render a distinct message per type."""

from __future__ import annotations


class ApiError(RuntimeError):
    """Base class for every stewards API failure."""


class ApiUnavailable(ApiError):
    """The API could not be reached, timed out, or returned 5xx."""


class ApiUnauthorized(ApiError):
    """The API rejected our token (401/403)."""


class ApiNotFound(ApiError):
    """The requested resource does not exist (404)."""


class ApiContractError(ApiError):
    """The API responded, but not in the shape this app models."""
