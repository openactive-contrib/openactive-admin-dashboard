"""`python -m stewards.outreach [--dry-run]`: raise today's problems on the notification list.

Reads every monitor the rules need and the whole notification list, decides, then creates
and updates rows. Nothing is written unless every read succeeded, every monitor answered for
today's snapshot and every list arrived complete: dropping a problem as resolved on the
strength of a partial read would be worse than not running.

Reads `ADMIN_API_BASE_URL` and `ADMIN_API_TOKEN` from the environment. Writes a markdown
report to `$GITHUB_STEP_SUMMARY` when that is set. Exits 1 when a gate fails or a write is
rejected; the writes that succeeded stay written, and the next run picks up the rest.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from stewards.alerts.__main__ import TIMEOUT, MissingConfig, admin_settings
from stewards.alerts.evaluate import uk_today
from stewards.api.client import StewardsClient
from stewards.api.errors import ApiError
from stewards.api.models import Incident, PublisherNotification
from stewards.api.repository import (
    _create_publisher_notification,
    _fetch_incidents,
    _fetch_publisher_notifications,
    _update_publisher_notification,
)
from stewards.outreach.decide import Snapshot, decide
from stewards.outreach.reconcile import Create, Plan, Update, reconcile
from stewards.outreach.rules import RULES, monitor_ids, validate


class GateFailed(RuntimeError):
    """A read the decision depends on is missing, stale or incomplete."""


def read_snapshot(client: StewardsClient, today: date) -> Snapshot:
    """Today's incidents for every monitor the rules read, or `GateFailed`.

    No `as_of` is sent: the admin API echoes a requested date back whether or not that day
    has landed, so only the latest-day read says what the batch really holds.
    """
    incidents: dict[str, tuple[Incident, ...]] = {}
    problems: list[str] = []
    for monitor_id in monitor_ids(RULES):
        try:
            page = _fetch_incidents(monitor_id, client, latest=True)
        except ApiError as exc:
            problems.append(f"{monitor_id}: {exc}")
            continue
        if page.meta.snapshot_date != today:
            problems.append(
                f"{monitor_id}: snapshot is {page.meta.snapshot_date}, not {today.isoformat()}"
            )
        elif len(page.data) != page.meta.total:
            problems.append(
                f"{monitor_id}: read {len(page.data)} of {page.meta.total} incidents"
            )
        incidents[monitor_id] = page.data
    if problems:
        raise GateFailed("; ".join(problems))
    return Snapshot(incidents)


def read_rows(client: StewardsClient) -> tuple[PublisherNotification, ...]:
    try:
        page = _fetch_publisher_notifications(client)
    except ApiError as exc:
        raise GateFailed(f"publisher notifications: {exc}") from exc
    if len(page.data) != page.meta.total:
        raise GateFailed(
            f"publisher notifications: read {len(page.data)} of {page.meta.total} rows"
        )
    return page.data


def apply(client: StewardsClient, plan: Plan) -> list[str]:
    """Write every action, carrying on past a rejected one. Returns the failures."""
    failures = []
    for action in plan.actions:
        try:
            if isinstance(action, Create):
                _create_publisher_notification(action.body, client)
            else:
                _update_publisher_notification(action.body, client)
        except ApiError as exc:
            failures.append(f"{action.body.id} ({action.publisher_name}): {exc}")
    return failures


def report(plan: Plan, today: date, *, dry_run: bool, failures: Sequence[str] = ()) -> str:
    """The run as markdown, for the job log and the step summary. Never a contact address."""
    created = [a for a in plan.actions if isinstance(a, Create)]
    updated = [a for a in plan.actions if isinstance(a, Update)]
    title = f"Publisher notifications for {today.isoformat()}" + (
        " (dry run)" if dry_run else ""
    )
    lines = [
        f"## {title}",
        "",
        f"{len(created)} row(s) to create, {len(updated)} to update, "
        f"{len(plan.skipped)} finding(s) not raised.",
    ]
    if plan.actions:
        lines += [
            "",
            "| Publisher | Row | Action | Added | Refreshed | Dropped |",
            "|---|---|---|---|---|---|",
        ]
        for action in plan.actions:
            added = ", ".join(f.rule.id for f in action.added) or "-"
            if isinstance(action, Create):
                lines.append(
                    f"| {action.publisher_name} | {action.body.id} | create | {added} | - | - |"
                )
            else:
                verb = "update, all resolved" if action.resolved else "update"
                lines.append(
                    f"| {action.publisher_name} | {action.body.id} | {verb} | {added} "
                    f"| {action.refreshed} | {action.dropped} |"
                )
    if plan.skipped:
        lines += ["", "**Not raised**", ""]
        for skip in plan.skipped:
            incident = skip.finding.incident
            what = incident.feed_name or skip.finding.dataset_url or incident.publisher_name
            lines.append(
                f"- {skip.finding.rule.id}, {incident.publisher_name} ({what}): {skip.reason}"
            )
    if plan.unreadable:
        lines += ["", f"Left alone, problems unreadable: {', '.join(plan.unreadable)}"]
    if failures:
        lines += ["", "**Failed writes**", "", *(f"- {failure}" for failure in failures)]
    return "\n".join(lines) + "\n"


def run(
    env: Mapping[str, str],
    *,
    now: datetime,
    dry_run: bool = False,
    api_transport: httpx.BaseTransport | None = None,
) -> int:
    validate(RULES)
    today = uk_today(now)
    client = StewardsClient(admin_settings(env), api_transport, TIMEOUT)
    try:
        try:
            snapshot = read_snapshot(client, today)
            rows = read_rows(client)
        except GateFailed as exc:
            print(f"Nothing written: {exc}", file=sys.stderr)
            _summarise(
                env,
                f"## Publisher notifications for {today.isoformat()}\n\n"
                f"Nothing written: {exc}\n",
            )
            return 1
        plan = reconcile(snapshot, decide(snapshot), rows, today)
        if dry_run:
            for action in plan.actions:
                body = action.body.to_payload()
                body.pop("contact", None)
                print(json.dumps(body, indent=2))
            failures: list[str] = []
        else:
            failures = apply(client, plan)
    finally:
        client.close()
    text = report(plan, today, dry_run=dry_run, failures=failures)
    print(text)
    _summarise(env, text)
    return 1 if failures else 0


def _summarise(env: Mapping[str, str], text: str) -> None:
    path = env.get("GITHUB_STEP_SUMMARY", "").strip()
    if path:
        with Path(path).open("a", encoding="utf-8") as summary:
            summary.write(text)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stewards.outreach")
    parser.add_argument("--dry-run", action="store_true", help="print the rows, write nothing")
    args = parser.parse_args(argv)
    try:
        return run(os.environ, now=datetime.now(UTC), dry_run=args.dry_run)
    except MissingConfig as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
