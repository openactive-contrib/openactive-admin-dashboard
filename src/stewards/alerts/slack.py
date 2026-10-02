"""Slack payloads for the daily digest, and the webhook post.

Block Kit with a plain `text` fallback (what notifications show). The webhook URL is a
credential, so nothing here logs it or puts it in an error message.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from stewards.alerts.evaluate import CheckResult, Digest, Hit

#: Bullets per section before "and N more". Slack caps a section's text at 3000 characters.
MAX_HITS = 25
TIMEOUT = httpx.Timeout(10.0, connect=3.0)


class SlackError(RuntimeError):
    """The webhook did not accept the message."""


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _title(today: date) -> str:
    title = f"OpenActive incidents {today.isoformat()}"
    if today.weekday() == 0:
        title += " (Monday, includes the weekend)"
    return title


def _hit_line(hit: Hit, field_label: str) -> str:
    name = _escape(hit.subject or "unnamed")
    if hit.link:
        name = f"<{hit.link}|{name}>"
    parts = [f"*{_escape(hit.publisher)}*", name]
    if hit.feed_type:
        parts.append(_escape(hit.feed_type))
    parts.append(f"{field_label}: {hit.value}")
    return "• " + " · ".join(parts)


def _section_text(result: CheckResult) -> str:
    check = result.check
    if result.error is not None:
        return f"*{check.label}*: could not be checked ({_escape(result.error)})"
    heading = f"*{check.label}* ({check.field_label} = {check.threshold}): {len(result.hits)}"
    lines = [heading]
    lines += [_hit_line(h, check.field_label) for h in result.hits[:MAX_HITS]]
    if len(result.hits) > MAX_HITS:
        lines.append(f"and {len(result.hits) - MAX_HITS} more")
    return "\n".join(lines)


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def build_digest(digest: Digest) -> dict[str, Any]:
    title = _title(digest.today)
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": title}}
    ]
    failed = [r for r in digest.results if r.error is not None]
    if digest.total == 0 and not failed:
        blocks.append(_section("No incidents crossed a threshold today."))
        fallback = f"{title}: no incidents crossed a threshold"
    else:
        blocks += [_section(_section_text(r)) for r in digest.results]
        fallback = f"{title}: {digest.total} incident(s) crossed a threshold"
        if failed:
            fallback += f", {len(failed)} check(s) failed"
    return {"text": fallback, "blocks": blocks}


def build_api_down(error: str, today: date) -> dict[str, Any]:
    text = f"The OpenActive admin API is not responding ({today.isoformat()}): {error}"
    return {
        "text": text,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Admin API is down"}},
            _section(_escape(text) + "\nNo incident checks were run today."),
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
