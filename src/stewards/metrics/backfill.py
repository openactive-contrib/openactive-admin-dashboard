"""`python -m stewards.metrics.backfill [--months N] [--dry-run]`: recover the crawl series.

`datasets_crawled` is the one measure in the framework with any history behind it. The
growth endpoint derives it month by month from the ingestion table, which reaches back
further than the capture job has been running, so those months can be recovered rather
than waited for. Everything else in the store has to be captured as it happens.

Run once. Months already captured are left alone, so a second run writes nothing, and a
month still in progress is skipped — its figure covers the days so far and changes daily,
and a captured measurement cannot be corrected. The monthly capture picks it up once it
has ended.

Reads `ADMIN_API_BASE_URL` and `ADMIN_API_TOKEN`. The analytics token is not needed: this
reads one admin endpoint. Exits 1 when the read fails or a write is rejected.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import httpx

from stewards.alerts.__main__ import TIMEOUT, MissingConfig, admin_settings
from stewards.alerts.evaluate import uk_today
from stewards.api.client import StewardsClient
from stewards.api.errors import ApiError
from stewards.api.models import EcosystemGrowthResponse, MetricMeasurement
from stewards.api.repository import _fetch_ecosystem_growth, _fetch_metric_snapshots
from stewards.metrics import measurements
from stewards.metrics.__main__ import _summarise, capture, unstored

FIELD = "datasets_crawled"

#: How far back to ask. The endpoint clamps this to 60, and the ingestion table does not
#: reach that far anyway, so it simply means "everything there is".
MONTHS = 60


def crawl_measurements(series: EcosystemGrowthResponse) -> tuple[MetricMeasurement, ...]:
    """One measurement per finished month in the series, oldest first.

    Each month goes through the same builder the monthly capture uses, so the rule about
    a month still in progress is stated in one place rather than two.
    """
    rows: list[MetricMeasurement] = []
    for point in series.data:
        rows.extend(measurements.ecosystem_measurements(point.period_start, series))
    return tuple(sorted(rows, key=lambda row: row.period))


def report(
    found: Sequence[MetricMeasurement],
    batch: Sequence[MetricMeasurement],
    *,
    dry_run: bool,
    problems: Sequence[str] = (),
) -> str:
    """The run as markdown, for the job log and the step summary."""
    title = "Crawl figure backfill" + (" (dry run)" if dry_run else "")
    lines = [
        f"## {title}",
        "",
        f"{len(batch)} month(s) to capture, {len(found) - len(batch)} already stored.",
    ]
    if batch:
        lines += ["", "| Month | Datasets crawled | Days with data |", "|---|---|---|"]
        for row in batch:
            value = "null" if row.value is None else f"{row.value:,.0f}"
            days = "" if row.denominator is None else f"{row.denominator:,.0f}"
            lines.append(f"| {row.period:%B %Y} | {value} | {days} |")
    if problems:
        lines += ["", "**Not captured**", "", *(f"- {problem}" for problem in problems)]
    return "\n".join(lines) + "\n"


def run(
    env: Mapping[str, str],
    *,
    now: datetime,
    months: int = MONTHS,
    dry_run: bool = False,
    api_transport: httpx.BaseTransport | None = None,
) -> int:
    today = uk_today(now)
    client = StewardsClient(admin_settings(env), api_transport, TIMEOUT)
    problems: list[str] = []
    try:
        try:
            series = _fetch_ecosystem_growth(client, as_of=today, months=months)
            stored = _fetch_metric_snapshots(
                client, sources=(measurements.ECOSYSTEM_GROWTH,), fields=(FIELD,)
            ).data
        except ApiError as exc:
            print(f"Nothing captured: {exc}", file=sys.stderr)
            _summarise(env, f"## Crawl figure backfill\n\nNothing captured: {exc}\n")
            return 1

        found = crawl_measurements(series)
        batch = unstored(found, stored)
        if batch and not dry_run:
            problems = capture(client, batch)
    finally:
        client.close()

    text = report(found, batch, dry_run=dry_run, problems=problems)
    print(text)
    _summarise(env, text)
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stewards.metrics.backfill")
    parser.add_argument(
        "--months", type=int, default=MONTHS, help=f"how far back to ask, default {MONTHS}"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the months, write nothing"
    )
    args = parser.parse_args(argv)
    try:
        return run(os.environ, now=datetime.now(UTC), months=args.months, dry_run=args.dry_run)
    except MissingConfig as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
