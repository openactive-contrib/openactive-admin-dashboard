"""The Slack payloads for the daily digest."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from stewards.alerts import slack
from stewards.alerts.checks import CHECKS
from stewards.alerts.evaluate import CheckResult, Digest, Hit

TUESDAY = date(2026, 10, 6)
MONDAY = date(2026, 10, 5)
CHECK = CHECKS[1]


def hit(n: int = 0, **kw: object) -> Hit:
    base: dict[str, object] = {
        "publisher": f"Pub <{n}>",
        "subject": "slots",
        "feed_type": "Slot",
        "value": 10,
        "link": "https://example.org/slots",
    }
    return Hit(**{**base, **kw})  # type: ignore[arg-type]


def texts(payload: dict[str, object]) -> str:
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    return "\n".join(b["text"]["text"] for b in blocks)


def test_digest_with_hits() -> None:
    payload = slack.build_digest(
        Digest(TUESDAY, (CheckResult(CHECK, (hit(),)), CheckResult(CHECKS[0])))
    )
    body = texts(payload)
    assert "OpenActive incidents 2026-10-06" in body
    assert "Single feed stalls* (Days stalled = 10): 1" in body
    line = "*Pub &lt;0&gt;* · <https://example.org/slots|slots> · Slot · Days stalled: 10"
    assert line in body
    assert "Dataset-wide stalls* (Days stalled = 5): 0" in body
    assert payload["text"] == (
        "OpenActive incidents 2026-10-06: 1 incident(s) crossed a threshold"
    )


def test_digest_with_nothing() -> None:
    payload = slack.build_digest(Digest(MONDAY, (CheckResult(CHECK),)))
    assert "No incidents crossed a threshold today." in texts(payload)
    assert "Monday, includes the weekend" in texts(payload)


def test_digest_with_no_checks() -> None:
    assert "No incidents" in texts(slack.build_digest(Digest(TUESDAY)))


def test_failed_check_is_reported_not_zero() -> None:
    payload = slack.build_digest(Digest(TUESDAY, (CheckResult(CHECK, error="boom"),)))
    assert "Single feed stalls*: could not be checked (boom)" in texts(payload)
    assert str(payload["text"]).endswith("1 check(s) failed")


def test_hit_without_link_or_subject() -> None:
    line = slack._hit_line(hit(subject=None, link=None, feed_type=None), "Days")
    assert line == "• *Pub &lt;0&gt;* · unnamed · Days: 10"


def test_truncation_at_cap() -> None:
    hits = tuple(hit(n) for n in range(slack.MAX_HITS + 3))
    body = texts(slack.build_digest(Digest(TUESDAY, (CheckResult(CHECK, hits),))))
    assert body.count("• ") == slack.MAX_HITS
    assert "and 3 more" in body


def test_exactly_cap_is_not_truncated() -> None:
    hits = tuple(hit(n) for n in range(slack.MAX_HITS))
    assert "more" not in texts(slack.build_digest(Digest(TUESDAY, (CheckResult(CHECK, hits),))))


def test_api_down() -> None:
    payload = slack.build_api_down("timed out", TUESDAY)
    assert "Admin API is down" in str(payload["blocks"])
    assert "timed out" in str(payload["text"])


def test_post_ok() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    slack.post("https://hooks.test/x", {"text": "hi"}, httpx.MockTransport(handler))
    assert seen[0].method == "POST"
    assert b'"text":"hi"' in seen[0].content.replace(b" ", b"")


def test_post_rejected_does_not_leak_url() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(403))
    with pytest.raises(slack.SlackError, match="403") as exc:
        slack.post("https://hooks.test/secret", {}, transport)
    assert "secret" not in str(exc.value)


def test_post_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    with pytest.raises(slack.SlackError, match="unreachable"):
        slack.post("https://hooks.test/x", {}, httpx.MockTransport(handler))
