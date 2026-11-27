"""`python -m stewards.metrics [--period YYYY-MM-01] [--dry-run]`: capture one month.

Reads the day's figures from five endpoints and stores them against a month, so the
measures defined as a trend have a series to read. The endpoints keep no history, so a
month nobody captures cannot be recovered afterwards. Defaults to the month just ended.

Only what is missing is sent. The run begins by reading what the store already holds for
the period and leaves those measurements alone, so a month captured in part — a read that
failed the first time — is completed by the next run rather than refused as a duplicate.
A month already captured in full writes nothing and exits 0; that is what the repeated
schedule is for.

Reads `ADMIN_API_BASE_URL` and `ADMIN_API_TOKEN` from the environment, the same two the
other jobs use. Writes a markdown report to `$GITHUB_STEP_SUMMARY` when that is set. Exits
1 when a read failed, when a write was rejected, or when the period is one that cannot be
captured.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from stewards.alerts.__main__ import TIMEOUT, MissingConfig, admin_settings
from stewards.alerts.evaluate import uk_today
from stewards.api.client import StewardsClient
from stewards.api.errors import ApiConflict, ApiError
from stewards.api.models import MetricMeasurement
from stewards.api.repository import (
    QUALITY_MONITOR_ID,
    _capture_metrics,
    _fetch_activity_summary,
    _fetch_analytics_summary,
    _fetch_ecosystem_growth,
    _fetch_metric_snapshots,
    _fetch_quality,
    _fetch_summary,
)
from stewards.metrics import measurements

ACTIVITY_MONITOR_ID = "activity_coverage"


class PeriodRefused(RuntimeError):
    """The month asked for is not one that can be captured."""


def previous_month(today: date) -> date:
    """The first day of the month before the one `today` falls in."""
    first = today.replace(day=1)
    if first.month == 1:
        return first.replace(year=first.year - 1, month=12)
    return first.replace(month=first.month - 1)


def check_period(period: date, today: date) -> None:
    """A period is the first of a month, and that month has ended.

    A month still running has no month-end figures to record, and a measurement cannot be
    corrected once captured — so a mistyped period would be permanent.
    """
    if period.day != 1:
        raise PeriodRefused(f"{period.isoformat()} is not the first of a month")
    if period >= today.replace(day=1):
        raise PeriodRefused(f"{period:%B %Y} has not ended yet")


@dataclass(frozen=True, slots=True)
class Collected:
    """What a run found: the month's measurements, and the reads that did not answer."""

    measurements: tuple[MetricMeasurement, ...]
    failures: tuple[str, ...]


def collect(client: StewardsClient, period: date, today: date) -> Collected:
    """Every measurement the month needs, carrying on past a read that fails.

    A failed read costs its own measurements and nothing else: the rest are still captured,
    and the next run fills the gap. The alternative — abandoning the month because one
    endpoint was down — risks losing it altogether.
    """
    rows: list[MetricMeasurement] = []
    failures: list[str] = []

    def read(name: str, build: Callable[[], tuple[MetricMeasurement, ...]]) -> None:
        try:
            rows.extend(build())
        except ApiError as exc:
            failures.append(f"{name}: {exc}")

    read(
        "feed quality",
        lambda: measurements.feed_quality_measurements(
            period, _fetch_quality(QUALITY_MONITOR_ID, client, as_of=today)
        ),
    )
    read(
        "activity coverage",
        lambda: measurements.activity_measurements(
            period, _fetch_activity_summary(ACTIVITY_MONITOR_ID, client, as_of=today)
        ),
    )
    read(
        "fleet summary",
        lambda: measurements.summary_measurements(period, _fetch_summary(client, as_of=today)),
    )
    # The public surface, not `/admin`. The API checks it against `Api:AccessToken` rather
    # than `Api:AdminToken`, but this deployment gives both the same value, so it rides on
    # the same client.
    read(
        "analytics summary",
        lambda: measurements.analytics_measurements(period, _fetch_analytics_summary(client)),
    )
    read(
        "ecosystem growth",
        lambda: measurements.ecosystem_measurements(
            period, _fetch_ecosystem_growth(client, as_of=today)
        ),
    )
    return Collected(tuple(rows), tuple(failures))


def key(measurement: MetricMeasurement) -> str:
    """How the store identifies a measurement: one per period, source and field."""
    return f"{measurement.period.isoformat()}|{measurement.source}|{measurement.field}"


def unstored(
    found: Sequence[MetricMeasurement], stored: Sequence[MetricMeasurement]
) -> tuple[MetricMeasurement, ...]:
    """The measurements the store does not hold yet, in the order they were built."""
    held = {key(measurement) for measurement in stored}
    return tuple(m for m in found if key(m) not in held)


def capture(client: StewardsClient, batch: Sequence[MetricMeasurement]) -> list[str]:
    """Write the batch. Returns the problems, empty when it landed.

    A refusal naming every measurement sent is not a problem: it is what a second run looks
    like when another has just captured the month. One naming only some is, because the
    store refuses in full and the rest went unwritten.
    """
    try:
        _capture_metrics(batch, client)
    except ApiConflict as exc:
        clashes = _clashes(exc)
        if len(clashes) == len(batch):
            return []
        return [f"already captured: {', '.join(clashes)}"] if clashes else [str(exc)]
    except ApiError as exc:
        return [str(exc)]
    return []


def _clashes(exc: ApiConflict) -> list[str]:
    """The measurements a refusal named, where it named any."""
    detail = exc.detail
    if not isinstance(detail, dict):
        return []
    listed = detail.get("measurements")
    return [str(item) for item in listed] if isinstance(listed, list) else []


def report(
    period: date,
    collected: Collected,
    batch: Sequence[MetricMeasurement],
    *,
    dry_run: bool,
    problems: Sequence[str] = (),
) -> str:
    """The run as markdown, for the job log and the step summary."""
    held = len(collected.measurements) - len(batch)
    title = f"Metric capture for {period:%B %Y}" + (" (dry run)" if dry_run else "")
    lines = [
        f"## {title}",
        "",
        f"{len(batch)} measurement(s) to capture, {held} already stored, "
        f"{len(collected.failures)} read(s) failed.",
    ]
    if batch:
        lines += [
            "",
            "| Source | Field | Value | Denominator | Snapshot |",
            "|---|---|---|---|---|",
        ]
        for m in batch:
            value = "null" if m.value is None else f"{m.value:,.4g}"
            denominator = "" if m.denominator is None else f"{m.denominator:,.0f}"
            snapshot = m.snapshot_date.isoformat() if m.snapshot_date else ""
            lines.append(f"| {m.source} | {m.field} | {value} | {denominator} | {snapshot} |")

    missing = [m for m in batch if m.value is None]
    if missing:
        lines += [
            "",
            f"{len(missing)} measurement(s) have no value and are stored as null, which "
            "the report reads as not available rather than as zero.",
        ]
    if collected.failures:
        lines += ["", "**Reads that failed**", "", *(f"- {f}" for f in collected.failures)]
    if problems:
        lines += ["", "**Not captured**", "", *(f"- {p}" for p in problems)]
    return "\n".join(lines) + "\n"


def run(
    env: Mapping[str, str],
    *,
    now: datetime,
    period: date | None = None,
    dry_run: bool = False,
    api_transport: httpx.BaseTransport | None = None,
) -> int:
    today = uk_today(now)
    month = period or previous_month(today)
    check_period(month, today)

    client = StewardsClient(admin_settings(env), api_transport, TIMEOUT)
    problems: list[str] = []
    try:
        collected = collect(client, month, today)
        try:
            stored = _fetch_metric_snapshots(client, start=month, end=month).data
        except ApiError as exc:
            print(f"Nothing captured, the store would not answer: {exc}", file=sys.stderr)
            _summarise(env, f"## Metric capture for {month:%B %Y}\n\nNothing captured: {exc}\n")
            return 1

        batch = unstored(collected.measurements, stored)
        if batch and not dry_run:
            problems = capture(client, batch)
    finally:
        client.close()

    text = report(month, collected, batch, dry_run=dry_run, problems=problems)
    print(text)
    _summarise(env, text)
    return 1 if problems or collected.failures else 0


def _summarise(env: Mapping[str, str], text: str) -> None:
    path = env.get("GITHUB_STEP_SUMMARY", "").strip()
    if path:
        with Path(path).open("a", encoding="utf-8") as summary:
            summary.write(text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stewards.metrics")
    parser.add_argument(
        "--period",
        help="first day of the month to capture, e.g. 2026-11-01; "
        "defaults to the month just ended",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the measurements, write nothing"
    )
    args = parser.parse_args(argv)
    try:
        period = date.fromisoformat(args.period) if args.period else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        return run(os.environ, now=datetime.now(UTC), period=period, dry_run=args.dry_run)
    except (MissingConfig, PeriodRefused) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
