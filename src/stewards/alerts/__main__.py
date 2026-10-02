"""`python -m stewards.alerts [--dry-run]`: check the admin API, post one Slack digest.

A day with no incidents and no failed checks posts nothing.

Reads `ADMIN_API_BASE_URL`, `ADMIN_API_TOKEN` and `SLACK_WEBHOOK_URL` from the environment.
Exits 1 when the API is down (after saying so in Slack) or when Slack rejects the post.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime

import httpx

from stewards.alerts import slack
from stewards.alerts.checks import CHECKS, Check
from stewards.alerts.evaluate import CheckResult, Digest, select, uk_today
from stewards.api.client import StewardsClient
from stewards.api.endpoints import Style
from stewards.api.errors import ApiError
from stewards.api.repository import _fetch_incidents, _fetch_summary
from stewards.config import Settings


class MissingConfig(RuntimeError):
    pass


def _require(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise MissingConfig(f"{key} is not set")
    return value


def admin_settings(env: Mapping[str, str]) -> Settings:
    return Settings(
        api_base_url=_require(env, "ADMIN_API_BASE_URL").rstrip("/"),
        api_token=_require(env, "ADMIN_API_TOKEN"),
        api_style=Style.ADMIN,
        api_token_param="token",
    )


def collect(client: StewardsClient, today: date, checks: Sequence[Check] = CHECKS) -> Digest:
    results = []
    for check in checks:
        try:
            page = _fetch_incidents(check.monitor_id, client, as_of=today)
        except ApiError as exc:
            results.append(CheckResult(check=check, error=str(exc)))
            continue
        results.append(select(check, page.data, today))
    return Digest(today=today, results=tuple(results))


def run(
    env: Mapping[str, str],
    *,
    now: datetime,
    dry_run: bool = False,
    api_transport: httpx.BaseTransport | None = None,
    slack_transport: httpx.BaseTransport | None = None,
) -> int:
    today = uk_today(now)
    webhook = "" if dry_run else _require(env, "SLACK_WEBHOOK_URL")
    client = StewardsClient(admin_settings(env), api_transport)

    def send(payload: dict[str, object]) -> None:
        if dry_run:
            print(json.dumps(payload, indent=2))
        else:
            slack.post(webhook, payload, slack_transport)

    try:
        try:
            _fetch_summary(client, as_of=today)
        except ApiError as exc:
            send(slack.build_api_down(str(exc), today))
            print(f"Admin API is down: {exc}", file=sys.stderr)
            return 1
        digest = collect(client, today)
        if not digest.is_empty:
            send(slack.build_digest(digest))
    finally:
        client.close()
    print(
        f"Digest for {today.isoformat()}: {digest.total} incident(s), "
        f"{digest.failed} failed check(s)" + (", nothing sent" if digest.is_empty else "")
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stewards.alerts")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the payload, post nothing"
    )
    args = parser.parse_args(argv)
    try:
        return run(os.environ, now=datetime.now(UTC), dry_run=args.dry_run)
    except (MissingConfig, slack.SlackError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
