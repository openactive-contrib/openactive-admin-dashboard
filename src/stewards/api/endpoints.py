"""Logical endpoint -> path and query string, per API shape.

Two shapes exist. `Style.CONTRACT` is the versioned REST contract this app was built
against — `/api/v1/monitors/<id>/incidents` — and `Style.ADMIN` is the interim admin API
on the stewards service, which exposes one pair of per-monitor paths derived from the
monitor id (`/admin/single-feed-stall-incidents` and `/admin/single-feed-stall-trend`),
and takes the snapshot it should answer for as `as_of`.

Both shapes route every read. An endpoint a deployment has not built yet answers 404,
which the client turns into `ApiNotFound` and the page renders as "registered in the
dashboard but its endpoint is not live in this deployment".

Pure: no httpx, no Streamlit, and no clock — `as_of` is passed in so routing is testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from urllib.parse import quote


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
    as_of: date | None,
    page: int = 1,
    page_size: int = 500,
) -> Endpoint:
    """A monitor's incidents. `as_of=None` sends no date, so the admin API answers for the
    latest day it has ingested and says which in `meta.snapshot_date`."""
    if style is Style.ADMIN:
        dated = {} if as_of is None else {"as_of": as_of.isoformat()}
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}-incidents",
            {**dated, "page": page, "page_size": page_size},
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


def quality(style: Style, monitor_id: str, *, as_of: date) -> Endpoint:
    """A monitor's quality snapshot: one row per feed, plus a fleet-wide summary block.

    The read a monitor declares instead of `incidents` when its batch reports measurements
    for the whole fleet rather than faults that age. The admin API answers it at the
    monitor's own slug with no suffix, because the snapshot *is* the resource — there is no
    incident list and no trend beside it.
    """
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}", {"as_of": as_of.isoformat()}
        )
    return Endpoint(f"/monitors/{monitor_id}/quality")


def summary(style: Style, *, as_of: date) -> Endpoint:
    """The fleet summary: overview KPIs, the monitor cards and the sidebar badges."""
    if style is Style.ADMIN:
        return Endpoint(f"{ADMIN_ROOT}/summary", {"as_of": as_of.isoformat()})
    return Endpoint("/summary")


def analytics_summary(style: Style) -> Endpoint:
    """The public analytics summary, the one read this app makes outside `/admin`.

    Not the fleet summary above. It is a different surface of the same service, with its
    own token, and it carries `number_of_facilities`, which no admin endpoint reports. The
    monthly metric capture is its only caller, so it takes no `as_of`: it reports whatever
    the service last published.
    """
    return Endpoint("/summary" if style is Style.ADMIN else "/analytics/summary")


def coverage(style: Style, monitor_id: str, *, as_of: date) -> Endpoint:
    """A monitor's coverage snapshot: this run's figures for a whole estate, and no rows.

    The third backing read, beside `incidents` and `quality`. Like a quality snapshot the
    admin API answers it at the monitor's own slug with no suffix, because the snapshot *is*
    the resource; unlike one, the rows behind it are a second resource of their own — see
    `coverage_mappings`.
    """
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}", {"as_of": as_of.isoformat()}
        )
    return Endpoint(f"/monitors/{monitor_id}/coverage")


def coverage_mappings(
    style: Style,
    monitor_id: str,
    rows_id: str,
    *,
    as_of: date,
    page: int = 1,
    page_size: int = 1000,
) -> Endpoint:
    """The rows behind a coverage snapshot, paginated.

    `rows_id` comes off the registry entry rather than being derived from the monitor id:
    the rows are their own resource with their own name, and guessing a suffix is how a path
    silently points at nothing.
    """
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(rows_id)}",
            {"as_of": as_of.isoformat(), "page": page, "page_size": page_size},
        )
    return Endpoint(f"/monitors/{monitor_id}/mappings", {"page": page, "page_size": page_size})


def custom_properties(
    style: Style,
    monitor_id: str,
    *,
    as_of: date,
    page: int = 1,
    page_size: int = 500,
) -> Endpoint:
    """A monitor's custom-property snapshot: one row per feed, plus a fleet summary block.

    The fourth backing read. The admin API answers it at the monitor's own slug with no
    suffix, as it does a quality snapshot, because the snapshot *is* the resource. Unlike a
    quality snapshot its rows page, so the page is named in both shapes.
    """
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}",
            {"as_of": as_of.isoformat(), "page": page, "page_size": page_size},
        )
    return Endpoint(
        f"/monitors/{monitor_id}/properties", {"page": page, "page_size": page_size}
    )


def activity_summary(style: Style, monitor_id: str, *, as_of: date) -> Endpoint:
    """An activity coverage monitor's national figures: one object, no rows.

    The fifth backing read is three resources under the monitor's own slug: this summary,
    the national ranking (`top_activities`) and the per-district rows (`activity_districts`).
    """
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}/summary", {"as_of": as_of.isoformat()}
        )
    return Endpoint(f"/monitors/{monitor_id}/summary")


def top_activities(style: Style, monitor_id: str, *, as_of: date, limit: int) -> Endpoint:
    """The nationally ranked activities, first `limit` of them: one page, never followed."""
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}/top-activities",
            {"as_of": as_of.isoformat(), "page": 1, "page_size": limit},
        )
    return Endpoint(f"/monitors/{monitor_id}/top-activities", {"page": 1, "page_size": limit})


def activity_districts(
    style: Style,
    monitor_id: str,
    *,
    as_of: date,
    page: int = 1,
    page_size: int = 1000,
) -> Endpoint:
    """One row per local authority district and activity, paginated. The admin API answers
    it at the monitor's slug with no suffix: the rows are the resource."""
    if style is Style.ADMIN:
        return Endpoint(
            f"{ADMIN_ROOT}/{monitor_slug(monitor_id)}",
            {"as_of": as_of.isoformat(), "page": page, "page_size": page_size},
        )
    return Endpoint(f"/monitors/{monitor_id}/districts", {"page": page, "page_size": page_size})


def ecosystem_growth(style: Style, *, as_of: date) -> Endpoint:
    """The ecosystem's monthly growth series: one row per calendar month, no paging.

    Not a monitor read, so it does not hang off a monitor id. The admin API names it
    `ecosystem-growth-trend`; the contract shape files it under `/ecosystem`.
    """
    if style is Style.ADMIN:
        return Endpoint(f"{ADMIN_ROOT}/ecosystem-growth-trend", {"as_of": as_of.isoformat()})
    return Endpoint("/ecosystem/growth")


# --- publisher notifications --------------------------------------------------------------
#
# Stored steward work rows rather than a daily snapshot: the same in both shapes, and no
# `as_of`, because a row is whatever was last saved, not a replay of a day's batch. The app
# writes here and to the metric snapshots below, and to nothing else.

NOTIFICATIONS = "publisher-notifications"


def _notifications_root(style: Style) -> str:
    return f"{ADMIN_ROOT}/{NOTIFICATIONS}" if style is Style.ADMIN else f"/{NOTIFICATIONS}"


def publisher_notifications(style: Style, *, page: int = 1, page_size: int = 500) -> Endpoint:
    """One page of the publisher notification list. Its path is also where a row is POSTed."""
    return Endpoint(_notifications_root(style), {"page": page, "page_size": page_size})


def publisher_notification_summary(style: Style) -> Endpoint:
    """Counts across the whole list, not the current page - its own resource by design."""
    return Endpoint(f"{_notifications_root(style)}/summary")


def publisher_notification(style: Style, notification_id: str) -> Endpoint:
    """One row, for PATCH and DELETE.

    The id is percent-encoded with no safe characters: it is typed by hand into BigQuery, so
    a `/` or `?` in it must not turn into a different path or a query string.
    """
    return Endpoint(f"{_notifications_root(style)}/{quote(notification_id, safe='')}")


# --- metric snapshots -----------------------------------------------------------------------
#
# The only read on this API that is a history. Every other endpoint reports current state,
# because the tables behind them are overwritten each night, so the measures defined as a
# trend have nothing to read until a month is captured here. No `as_of`: a row is a figure
# recorded for a month, not a replay of a day's batch. This path is also where the monthly
# capture job POSTs its measurements.

METRIC_SNAPSHOTS = "metric-snapshots"


def metric_snapshots(
    style: Style,
    *,
    sources: Sequence[str] = (),
    fields: Sequence[str] = (),
    start: date | None = None,
    end: date | None = None,
    page: int = 1,
    page_size: int = 500,
) -> Endpoint:
    """The captured series, newest period first. Its path is also where a batch is POSTed.

    Every filter is optional, and leaving one out means every value rather than none.
    `sources` and `fields` go as one comma-separated `source` and `field`, which the API
    reads as "any of these". `start` and `end` are inclusive and compare against the
    period; they travel as `from` and `to`, neither of which can be a parameter name here.
    """
    root = (
        f"{ADMIN_ROOT}/{METRIC_SNAPSHOTS}" if style is Style.ADMIN else f"/{METRIC_SNAPSHOTS}"
    )
    params: dict[str, str | int] = {}
    if sources:
        params["source"] = ",".join(sources)
    if fields:
        params["field"] = ",".join(fields)
    if start is not None:
        params["from"] = start.isoformat()
    if end is not None:
        params["to"] = end.isoformat()
    return Endpoint(root, {**params, "page": page, "page_size": page_size})
