"""The Slack payloads for the daily digest."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from stewards.alerts import slack
from stewards.alerts.checks import CHECKS
from stewards.alerts.evaluate import CheckResult, Digest, Hit

TUESDAY = date(2026, 10, 6)
MONDAY = date(2026, 10, 5)
DASH = "https://dash.test/"
DATASET, FEED, INGESTION = CHECKS


def hit(n: int = 0, **kw: object) -> Hit:
    base: dict[str, object] = {
        "publisher": f"Pub <{n}>",
        "subject": "slots",
        "feed_type": "Slot",
        "value": 10,
        "link": "https://example.org/slots",
    }
    return Hit(**{**base, **kw})  # type: ignore[arg-type]


def body(payload: dict[str, object]) -> str:
    return json.dumps(payload["blocks"], ensure_ascii=False)


def buttons(payload: dict[str, object]) -> list[str]:
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    return [e["url"] for b in blocks if b["type"] == "actions" for e in b["elements"]]


@pytest.mark.parametrize(
    ("monitor_id", "route"),
    [
        ("dataset_stall", "dataset_stalls"),
        ("single_feed_stall", "single_feed_stalls"),
        ("feed_ingestion_error", "feed_ingestion_errors"),
    ],
)
def test_page_url_drops_the_numeric_prefix(monitor_id: str, route: str) -> None:
    assert slack.page_url(DASH, monitor_id) == f"https://dash.test/{route}"
    assert slack.page_url("https://dash.test", monitor_id) == f"https://dash.test/{route}"


def test_digest_summarises_and_links() -> None:
    digest = Digest(
        TUESDAY,
        (CheckResult(DATASET), CheckResult(FEED, (hit(),)), CheckResult(INGESTION)),
        snapshot_date=TUESDAY,
    )
    payload = slack.build_digest(digest, DASH)
    text = body(payload)

    assert "*1 new incident* since yesterday" in text
    assert "*<https://dash.test/single_feed_stalls|Single feed stalls>*: 1 new incident" in text
    assert (
        "• Pub &lt;0&gt;: <https://example.org/slots|slots>, Slot (10 days without new data)"
        in text
    )
    assert "Nothing new: Dataset-wide stalls, Feed ingestion errors" in text
    assert "daily snapshot of 2026-10-06" in text
    assert buttons(payload) == [DASH]
    assert payload["text"] == "OpenActive daily incident summary, 2026-10-06: 1 new incident"


def test_plurals_and_monday_wording() -> None:
    payload = slack.build_digest(Digest(MONDAY, (CheckResult(INGESTION, (hit(), hit(1))),)))
    text = body(payload)
    assert "*2 new incidents* since Friday" in text
    assert "(10 failed runs in a row)" in text
    assert "Nothing new" not in text
    assert "snapshot" not in text
    assert buttons(payload) == [slack.DEFAULT_DASHBOARD_URL]


def test_failed_check_is_called_out() -> None:
    payload = slack.build_digest(Digest(TUESDAY, (CheckResult(FEED, error="boom"),)), DASH)
    text = body(payload)
    assert "No new incidents since yesterday, but some checks could not run" in text
    assert "We could not check *Single feed stalls* today" in text
    assert str(payload["text"]).endswith("0 new incidents, 1 check could not run")


def test_example_without_link_subject_or_type() -> None:
    line = slack._example(hit(subject=None, link=None, feed_type=None), CheckResult(FEED))
    assert line == "• Pub &lt;0&gt;: unnamed (10 days without new data)"


def test_examples_cap_with_more_link() -> None:
    hits = tuple(hit(n) for n in range(slack.MAX_EXAMPLES + 3))
    text = body(slack.build_digest(Digest(TUESDAY, (CheckResult(FEED, hits),))))
    assert text.count("• ") == slack.MAX_EXAMPLES
    assert "…and 3 more on the dashboard" in text


def test_exactly_cap_has_no_more_line() -> None:
    hits = tuple(hit(n) for n in range(slack.MAX_EXAMPLES))
    assert "more on the dashboard" not in body(
        slack.build_digest(Digest(TUESDAY, (CheckResult(FEED, hits),)))
    )


@pytest.mark.parametrize(
    ("results", "empty"),
    [
        ((), True),
        ((CheckResult(FEED),), True),
        ((CheckResult(FEED, (hit(),)),), False),
        ((CheckResult(FEED, error="boom"),), False),
    ],
    ids=["no-checks", "no-hits", "one-hit", "failed-check"],
)
def test_is_empty(results: tuple[CheckResult, ...], empty: bool) -> None:
    assert Digest(TUESDAY, results).is_empty is empty


def test_api_down() -> None:
    payload = slack.build_api_down("timed out <x>", TUESDAY, DASH)
    assert "Admin API is down" in body(payload)
    assert "checks did not run" in body(payload)
    assert "timed out &lt;x&gt;" in body(payload)
    assert buttons(payload) == [DASH]


def test_post_ok() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    slack.post("https://hooks.test/x", {"text": "hi"}, httpx.MockTransport(handler))
    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {"text": "hi"}


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
