"""Slack payloads for the daily digest, and the webhook post.

The message is a summary for people, not a dump of the table: a count per check, a few
example lines, and links into the dashboard for everything else. Block Kit with a plain
`text` fallback (what notifications show). The webhook URL is a credential, so nothing here
logs it or puts it in an error message.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath
from typing import Any

import httpx

from stewards.alerts.evaluate import CheckResult, Digest, Hit
from stewards.monitors.registry import get_monitor

DEFAULT_DASHBOARD_URL = (
    "https://openactive-admin-dashboard-hdb3fpcvcmgygydn.ukwest-01.azurewebsites.net/"
)

#: Example lines per check; the rest are one click away on the dashboard.
MAX_EXAMPLES = 5
TIMEOUT = httpx.Timeout(10.0, connect=3.0)


class SlackError(RuntimeError):
    """The webhook did not accept the message."""


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def page_url(dashboard_url: str, monitor_id: str) -> str:
    """The monitor's page in the dashboard.

    Streamlit routes a page by its filename without the numeric prefix, so
    `views/12_feed_ingestion_errors.py` is served at `/feed_ingestion_errors`.
    """
    stem = PurePosixPath(get_monitor(monitor_id).page).stem
    return f"{dashboard_url.rstrip('/')}/{re.sub(r'^\d+_', '', stem)}"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _button(dashboard_url: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Open the dashboard"},
                "url": dashboard_url,
                "style": "primary",
            }
        ],
    }


def _intro(digest: Digest) -> str:
    since = "since Friday" if digest.today.weekday() == 0 else "since yesterday"
    if digest.total == 0:
        return f"Good morning. No new incidents {since}, but some checks could not run."
    return (
        f"Good morning. *{_plural(digest.total, 'new incident')}* {since} "
        "need a look. Here is a summary."
    )


def _example(hit: Hit, result: CheckResult) -> str:
    name = _escape(hit.subject or "unnamed")
    if hit.link:
        name = f"<{hit.link}|{name}>"
    kind = f", {_escape(hit.feed_type)}" if hit.feed_type else ""
    return f"• {_escape(hit.publisher)}: {name}{kind} ({result.check.describe(hit.value)})"


def _check_section(result: CheckResult, dashboard_url: str) -> str:
    check = result.check
    title = f"*<{page_url(dashboard_url, check.monitor_id)}|{_escape(check.label)}>*"
    lines = [f"{title}: {_plural(len(result.hits), 'new incident')}"]
    lines += [_example(h, result) for h in result.hits[:MAX_EXAMPLES]]
    rest = len(result.hits) - MAX_EXAMPLES
    if rest > 0:
        lines.append(f"…and {rest} more on the dashboard")
    return "\n".join(lines)


def build_digest(digest: Digest, dashboard_url: str = DEFAULT_DASHBOARD_URL) -> dict[str, Any]:
    title = f"OpenActive daily incident summary, {digest.today.isoformat()}"
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        _section(_intro(digest)),
    ]
    found = [r for r in digest.results if r.hits]
    quiet = [r.check.label for r in digest.results if not r.hits and r.error is None]
    failed = [r for r in digest.results if r.error is not None]

    if found:
        blocks.append({"type": "divider"})
        blocks += [_section(_check_section(r, dashboard_url)) for r in found]
    if quiet:
        blocks.append(_context("Nothing new: " + ", ".join(_escape(q) for q in quiet)))
    if failed:
        names = ", ".join(_escape(r.check.label) for r in failed)
        blocks.append(
            _section(f"We could not check *{names}* today. The dashboard may still show it.")
        )
    blocks.append({"type": "divider"})
    blocks.append(_section("Full details, history and contact drafts are on the dashboard."))
    blocks.append(_button(dashboard_url))

    fallback = f"{title}: {_plural(digest.total, 'new incident')}"
    if failed:
        fallback += f", {_plural(len(failed), 'check')} could not run"
    return {"text": fallback, "blocks": blocks}


def build_api_down(
    error: str, today: date, dashboard_url: str = DEFAULT_DASHBOARD_URL
) -> dict[str, Any]:
    text = (
        "The OpenActive admin API is not responding this morning, so today's incident "
        "checks did not run."
    )
    return {
        "text": f"Admin API is down ({today.isoformat()})",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Admin API is down"}},
            _section(text),
            _context(f"{today.isoformat()} · {_escape(error)}"),
            _button(dashboard_url),
        ],
    }


def post(
    webhook_url: str, payload: dict[str, Any], transport: httpx.BaseTransport | None = None
) -> None:
    try:
        with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
            response = client.post(webhook_url, json=payload)
    except httpx.HTTPError as exc:
        raise SlackError(f"Slack webhook unreachable ({type(exc).__name__})") from exc
    if response.status_code >= 300:
        raise SlackError(f"Slack webhook returned {response.status_code}")
