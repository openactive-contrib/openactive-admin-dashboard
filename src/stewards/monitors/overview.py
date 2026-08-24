"""Overview tiles and sidebar labels, derived from the registry plus the summary counts."""

from __future__ import annotations

from dataclasses import dataclass

from stewards.api.models import Summary
from stewards.monitors.registry import MONITOR_REGISTRY, Monitor, Severity
from stewards.monitors.thresholds import Tone

STATE_LABELS = {
    Tone.RED: "Critical",
    Tone.AMBER: "Warning",
    Tone.GREEN: "Healthy",
    Tone.GREY: "Info",
}


def tile_state(monitor: Monitor, count: int, past_threshold_count: int) -> Tone:
    """Green with nothing open, red once anything is past the threshold, else amber.

    Informational monitors stay grey while they are merely non-zero.
    """
    if count <= 0:
        return Tone.GREEN
    if past_threshold_count > 0:
        return Tone.RED
    if monitor.severity is Severity.INFORMATIONAL:
        return Tone.GREY
    return Tone.AMBER


def tile_note(monitor: Monitor, count: int, past_threshold_count: int) -> str:
    if count <= 0:
        return "no open incidents in this snapshot"
    if past_threshold_count > 0:
        return f"{past_threshold_count} past the {monitor.threshold_days}-day threshold"
    return f"none past the {monitor.threshold_days}-day threshold yet"


#: A true minus sign (U+2212), not a hyphen: it aligns with the digits at this size.
MINUS = "\u2212"


def format_delta(change: int | None) -> str | None:
    """Signed change against the previous snapshot, or None when unknown.

    Zero is rendered explicitly: "no change" is information, whereas a missing field is not.
    """
    if change is None:
        return None
    if change == 0:
        return "0"
    return f"+{change:,}" if change > 0 else f"{MINUS}{abs(change):,}"


@dataclass(frozen=True, slots=True)
class NavBadge:
    """The count pill beside a sidebar item."""

    text: str
    tone: Tone


def nav_badges(summary: Summary) -> dict[str, NavBadge]:
    """Badge per sidebar item, keyed by monitor id plus `contact_queue`.

    A monitor with nothing open gets no badge, so the sidebar shows only what needs
    attention. Tone matches the monitor tile, so the sidebar and the overview never disagree.
    """
    badges: dict[str, NavBadge] = {}
    if summary.past_threshold > 0:
        badges["contact_queue"] = NavBadge(str(summary.past_threshold), Tone.RED)
    for monitor in MONITOR_REGISTRY:
        counts = summary.count_for(monitor.id)
        if counts is None or counts.count <= 0:
            continue
        badges[monitor.id] = NavBadge(
            str(counts.count), tile_state(monitor, counts.count, counts.past_threshold_count)
        )
    return badges


@dataclass(frozen=True, slots=True)
class Tile:
    monitor: Monitor
    count: int
    past_threshold_count: int
    state: Tone
    state_label: str
    note: str
    sparkline: tuple[float, ...]

    @property
    def value(self) -> str:
        return f"{self.count:,}"


def build_tiles(summary: Summary) -> tuple[Tile, ...]:
    """One tile per registered monitor, in registry order.

    A monitor the API does not yet report is shown at zero rather than hidden, so a registry
    entry landing before its API endpoint is visible instead of silently missing.
    """
    tiles = []
    for monitor in MONITOR_REGISTRY:
        counts = summary.count_for(monitor.id)
        count = counts.count if counts else 0
        past = counts.past_threshold_count if counts else 0
        state = tile_state(monitor, count, past)
        tiles.append(
            Tile(
                monitor=monitor,
                count=count,
                past_threshold_count=past,
                state=state,
                state_label=STATE_LABELS[state],
                note=tile_note(monitor, count, past),
                sparkline=counts.sparkline if counts else (),
            )
        )
    return tuple(tiles)


def sidebar_counts(summary: Summary) -> dict[str, int]:
    """Monitor id -> open incident count, for the navigation labels."""
    return {m.monitor_id: m.count for m in summary.monitors}
