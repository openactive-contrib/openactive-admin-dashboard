"""Logical endpoint -> path and query string, per API shape.

Two shapes exist. `Style.CONTRACT` is the versioned REST contract this app was built
against — `/api/v1/monitors/<id>/incidents` — and is the shape `sample_data/` serves.
`Style.ADMIN` is the interim admin API on the stewards service, which exposes one pair of
per-monitor paths derived from the monitor id (`/admin/single-feed-stall-incidents` and
`/admin/single-feed-stall-trend`), and takes the snapshot it should answer for as `as_of`.

Both shapes route all four reads. An endpoint a deployment has not built yet answers 404,
which the client turns into `ApiNotFound` and the page renders as "registered in the
dashboard but its endpoint is not live in this deployment".

Pure: no httpx, no Streamlit, and no clock — `as_of` is passed in so routing is testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class Style(StrEnum):
    CONTRACT = "contract"
    ADMIN = "admin"


#: Path prefix per shape, joined onto `STEWARDS_API_BASE_URL`. The admin API is mounted at
#: the service root, so it takes none.
PREFIX: dict[Style, str] = {Style.CONTRACT: "/api/v1", Style.ADMIN: ""}

ADMIN_ROOT = "/admin"


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One request: a path relative to the client's base URL, plus its query."""

    path: str
    params: dict[str, str | int] = field(default_factory=dict)


def prefix(style: Style) -> str:
    return PREFIX[style]


def monitor_slug(monitor_id: str) -> str:
    """`single_feed_stall` -> `single-feed-stall`, the admin API's path spelling."""
    return monitor_id.replace("_", "-")


def incidents(
    style: Style,
    monitor_id: str,
    *,
    as_of: date,
    page: int = 1,
    page_size: int = 500,
) -> Endpoint:
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}-incidents",
            {"as_of": as_of.isoformat(), "page": page, "page_size": page_size},
        )
    return Endpoint(f"/monitors/{monitor_id}/incidents", {"page": page, "page_size": page_size})


def trend(style: Style, monitor_id: str, *, as_of: date, days: int = 30) -> Endpoint:
    """The trend series.

    The admin path is singular (`…-trend`, unlike `…-incidents`), and the API decides the
    window itself, so no `days` is sent — asking for a parameter an endpoint does not
    implement is how a query silently means nothing.
    """
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}-trend", {"as_of": as_of.isoformat()}
        )
    return Endpoint(f"/monitors/{monitor_id}/trend", {"days": days})


def summary(style: Style, *, as_of: date) -> Endpoint:
    """The fleet summary: overview KPIs, the monitor cards and the sidebar badges."""
    if style is Style.ADMIN:
        return Endpoint(f"{ADMIN_ROOT}/summary", {"as_of": as_of.isoformat()})
    return Endpoint("/summary")


def contact_queue(style: Style, *, as_of: date) -> Endpoint:
    """The cross-monitor union of incidents at or past the threshold."""
    if style is Style.ADMIN:
        return Endpoint(f"{ADMIN_ROOT}/contact-queue", {"as_of": as_of.isoformat()})
    return Endpoint("/contact-queue")
