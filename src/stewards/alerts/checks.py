"""The alert rules: one `Check` per condition, read by the daily Slack digest.

Adding a condition is one entry in `CHECKS`. A check names the monitor whose incidents it
reads, the field it compares and the threshold; `field` and `link_field` use the same path
syntax as a registry column (`days_open`, `detail.dataset_name`), so they reach a monitor's
own measurements through its declared detail model. Pure: no I/O, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: `date.weekday()` of Monday. The digest runs on weekdays only, so Monday's run also
#: covers the two weekend days nobody looked at.
MONDAY = 0
WEEKEND_DAYS = 2


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    label: str
    """Section heading in the Slack message."""

    monitor_id: str
    """Registry id; decides which incident endpoint is read."""

    field: str
    """Path of the integer compared against `threshold`."""

    field_label: str
    threshold: int

    feed_types: frozenset[str] | None = None
    """Only incidents on these feed types count. None means any feed type."""

    link_field: str = "feed_url"

    value_text: str = "{value} days"
    """How one incident's figure reads in the message, e.g. "12 days"."""

    def describe(self, value: int) -> str:
        return self.value_text.format(value=value)

    def matches_value(self, value: object, today: date) -> bool:
        return isinstance(value, int) and value in matching_values(self.threshold, today)

    def matches_feed_type(self, feed_type: str | None) -> bool:
        return self.feed_types is None or feed_type in self.feed_types


def matching_values(threshold: int, today: date) -> range:
    """The values that count as "just crossed" on `today`.

    Exact crossings, so each incident is reported once. Monday widens the window by the two
    weekend days: a dataset that reached 5 days stalled on Saturday is 7 by Monday.
    """
    extra = WEEKEND_DAYS if today.weekday() == MONDAY else 0
    return range(threshold, threshold + extra + 1)


CHECKS: tuple[Check, ...] = (
    Check(
        id="dataset_stall",
        label="Dataset-wide stalls",
        monitor_id="dataset_stall",
        field="days_open",
        field_label="Days stalled",
        threshold=5,
        link_field="detail.dataset_url",
        value_text="{value} days without new data",
    ),
    Check(
        id="single_feed_stall",
        label="Single feed stalls",
        monitor_id="single_feed_stall",
        field="days_open",
        field_label="Days stalled",
        threshold=10,
        feed_types=frozenset({"Slot", "ScheduledSession"}),
        value_text="{value} days without new data",
    ),
    Check(
        id="feed_ingestion_error",
        label="Feed ingestion errors",
        monitor_id="feed_ingestion_error",
        field="days_open",
        field_label="Consecutive failures",
        threshold=10,
        value_text="{value} failed runs in a row",
    ),
)
