"""The publisher email draft — publisher-facing copy, so it is a golden-file test."""

from __future__ import annotations

from datetime import date

from stewards.api.models import Incident
from stewards.monitors.email_draft import REPLY_WINDOW_DAYS, draft_email, subject_line
from stewards.monitors.registry import get_monitor
from stewards.monitors.transforms import EMPTY

SNAPSHOT = date(2026, 8, 21)

STALL_GOLDEN = """\
Subject: OpenActive data check: scheduled-sessions — single-feed stalls

Hello Freedom Leisure team,

We monitor the OpenActive feeds you publish as part of the open data service.
In our snapshot of 2026-08-21, the feed still responds with HTTP 200, but the most recent \
modified timestamp has not advanced for 22 days. The last change we recorded was 2026-07-30.

Feed: scheduled-sessions
Feed type: ScheduledSession
Endpoint: https://opendata.freedom-leisure.example.org/openactive/scheduled-sessions
First detected: 2026-07-30 (22 days open)

Could you confirm whether the export that populates this feed is still running,
and let us know by 2026-08-26 if you need help investigating.

No action is needed on our side once the feed resumes; the check clears itself
on the next daily snapshot.

Thank you,
The ODI data stewards team"""


def stall_incident(**overrides: object) -> Incident:
    return Incident.model_validate(
        {
            "monitor_id": "single_feed_stall",
            "publisher_id": "pub_freedom-leisure",
            "publisher_name": "Freedom Leisure",
            "feed_id": "feed_freedom",
            "feed_name": "scheduled-sessions",
            "feed_type": "ScheduledSession",
            "feed_url": "https://opendata.freedom-leisure.example.org/openactive/scheduled-sessions",
            "first_detected": "2026-07-30",
            "days_open": 22,
            "past_threshold": True,
            "status": "contact_due",
            "detail": {"last_modified": "2026-07-30"},
        }
        | overrides
    )


def test_stall_draft_matches_the_golden_copy() -> None:
    draft = draft_email(get_monitor("single_feed_stall"), stall_incident(), SNAPSHOT)
    assert draft == STALL_GOLDEN


def test_draft_names_the_monitor_and_the_days_open() -> None:
    draft = draft_email(get_monitor("single_feed_stall"), stall_incident(), SNAPSHOT)
    assert "22 days" in draft
    assert "single-feed stalls" in draft


def test_ingestion_draft_uses_the_last_successful_ingestion() -> None:
    incident = stall_incident(
        monitor_id="feed_ingestion_error",
        days_open=11,
        detail={"error_code": "503", "last_completed": "2026-08-10"},
    )
    draft = draft_email(get_monitor("feed_ingestion_error"), incident, SNAPSHOT)
    assert "11 consecutive days" in draft
    assert "The last successful ingestion was 2026-08-10." in draft


def test_reply_window_is_measured_from_the_snapshot() -> None:
    draft = draft_email(get_monitor("single_feed_stall"), stall_incident(), SNAPSHOT)
    assert f"by {(SNAPSHOT.replace(day=SNAPSHOT.day + REPLY_WINDOW_DAYS)).isoformat()}" in draft


def test_missing_evidence_falls_back_to_first_detected() -> None:
    incident = stall_incident(detail={})
    draft = draft_email(get_monitor("single_feed_stall"), incident, SNAPSHOT)
    assert "The last change we recorded was 2026-07-30." in draft


def test_missing_optional_fields_render_as_em_dash() -> None:
    incident = stall_incident(feed_name=None, feed_type=None, feed_url=None)
    draft = draft_email(get_monitor("single_feed_stall"), incident, SNAPSHOT)
    assert f"Feed: {EMPTY}" in draft
    assert f"Endpoint: {EMPTY}" in draft
    assert "OpenActive feed" in draft


def test_boundary_incident_reads_naturally() -> None:
    draft = draft_email(get_monitor("single_feed_stall"), stall_incident(days_open=7), SNAPSHOT)
    assert "for 7 days" in draft
    assert "(7 days open)" in draft


def test_copy_tone_has_no_exclamation_or_emoji() -> None:
    draft = draft_email(get_monitor("single_feed_stall"), stall_incident(), SNAPSHOT)
    assert "!" not in draft
    # Punctuation such as the em dash is fine; pictographs are not.
    assert all(ord(char) < 0x2500 for char in draft)


def test_sender_name_is_overridable() -> None:
    draft = draft_email(
        get_monitor("single_feed_stall"), stall_incident(), SNAPSHOT, sender_name="A Steward"
    )
    assert draft.endswith("A Steward")


def test_subject_line_falls_back_when_the_feed_is_unnamed() -> None:
    subject = subject_line(get_monitor("feed_ingestion_error"), stall_incident(feed_name=None))
    assert subject == "OpenActive data check: OpenActive feed — feed ingestion errors"


# --- a monitor that reports a snapshot rather than an ageing fault -------------------------

ORPHAN_GOLDEN = """\
Subject: OpenActive data check: OpenActive feed — orphaned children

Hello Played team,

We monitor the OpenActive feeds you publish as part of the open data service.
In our snapshot of 2026-08-21, 91,748 items in this dataset reference a parent that the \
feed does not contain: ScheduledSessions whose superEvent is missing from the SessionSeries \
feed, or Slots without their FacilityUse. A consumer cannot display these items at all, \
because the parent carries the name, location and activity.

Feed: —
Feed type: —
Endpoint: —
First detected: — (this monitor reports a snapshot, not an age)

Could you confirm whether the export that populates this feed is still running,
and let us know by 2026-08-26 if you need help investigating.

No action is needed on our side once the feed resumes; the check clears itself
on the next daily snapshot.

Thank you,
The ODI data stewards team"""


def orphan_incident(**overrides: object) -> Incident:
    return Incident.model_validate(
        {
            "monitor_id": "dataset_orphaned_children",
            "publisher_id": "pub_played",
            "publisher_name": "Played",
            "past_threshold": True,
            "status": "open",
            "orphan_count": 91748,
            "dataset_name": "Played Sessions and Facilities",
            "detail": {"by_kind": []},
        }
        | overrides
    )


def test_orphan_draft_matches_the_golden_copy() -> None:
    monitor = get_monitor("dataset_orphaned_children")
    assert draft_email(monitor, orphan_incident(), SNAPSHOT) == ORPHAN_GOLDEN


def test_the_orphan_draft_cites_the_count_and_never_a_number_of_days() -> None:
    """There is no age to cite, and a fabricated one in a publisher email is worse than none."""
    monitor = get_monitor("dataset_orphaned_children")
    draft = draft_email(monitor, orphan_incident(), SNAPSHOT)
    assert "91,748 items" in draft
    assert "days open" not in draft
    assert "None" not in draft


def test_a_monitor_with_no_age_says_so_rather_than_inventing_a_date() -> None:
    monitor = get_monitor("dataset_orphaned_children")
    draft = draft_email(monitor, orphan_incident(), SNAPSHOT)
    assert f"First detected: {EMPTY} (this monitor reports a snapshot, not an age)" in draft


def test_a_first_detected_with_no_days_open_still_states_the_date() -> None:
    """A partial payload: the date is real, the age is not reported."""
    monitor = get_monitor("dataset_orphaned_children")
    draft = draft_email(monitor, orphan_incident(first_detected="2026-08-01"), SNAPSHOT)
    assert "First detected: 2026-08-01" in draft
    assert "days open" not in draft


def test_the_orphan_draft_falls_back_when_the_count_is_absent() -> None:
    monitor = get_monitor("dataset_orphaned_children")
    draft = draft_email(monitor, orphan_incident(detail={}, orphan_count=None), SNAPSHOT)
    assert f"In our snapshot of 2026-08-21, {EMPTY} items" in draft
