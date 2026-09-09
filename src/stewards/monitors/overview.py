"""Overview tiles and sidebar labels, derived from the registry plus the daily series.

A tile's state is `monitors.health` run over the monitor's own trend, not a hand-set count
threshold — see that module for the arithmetic. This one only decides which series the
verdict is taken from, and how it reads on a card.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stewards.api.models import MonitorCount, Summary, TrendPoint
from stewards.monitors.health import Health, HealthState, Movement, assess_monitor
from stewards.monitors.registry import MONITOR_REGISTRY, Monitor, Severity
from stewards.monitors.thresholds import Tone
from stewards.monitors.transforms import EMPTY

#: Trend series by monitor id, as the overview loads them. A monitor missing from the
#: mapping is judged on the sparkline `/summary` carries instead.
Trends = Mapping[str, Sequence[TrendPoint]]

NOT_REPORTED = "count not reported in this snapshot"

#: Points below which a card says nothing about its trend, as with the tile sparkline.
MIN_TREND_POINTS = 2

#: What an informational monitor's tile says instead of "Warning": it has something open,
#: but the monitor is context rather than a fault to chase.
INFO_LABEL = "Info"


def format_count(count: int | None) -> str:
    """A count for display. None is a figure the API did not report, never a zero."""
    return EMPTY if count is None else f"{count:,}"


def series_from_trend(
    points: Sequence[TrendPoint],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The open series and the past-threshold series, oldest first."""
    ordered = sorted(points, key=lambda point: point.date)
    return (
        tuple(float(point.open_count) for point in ordered),
        tuple(float(point.past_threshold_count) for point in ordered),
    )


def series_from_counts(
    counts: MonitorCount,
) -> tuple[tuple[float | None, ...], tuple[float | None, ...]]:
    """The same two series taken from `/summary` alone, for a monitor with no trend endpoint.

    The sparkline is history and `count` is the authoritative figure for this snapshot, so
    the count is appended only when the sparkline does not already end on it. The
    past-threshold figure arrives as a single number, which carries a level but no movement —
    exactly what `assess_monitor` treats as "not falling".
    """
    history = tuple(counts.sparkline)
    latest = None if counts.count is None else float(counts.count)
    values = history if history and history[-1] == latest else (*history, latest)
    past = () if counts.past_threshold_count is None else (float(counts.past_threshold_count),)
    return values, past


def monitor_health(
    monitor: Monitor, counts: MonitorCount, trend: Sequence[TrendPoint] = ()
) -> Health:
    """A monitor's state, judged on its trend series where the deployment serves one.

    A null count is decisive whatever the history says: the snapshot did not report this
    monitor's figure, and an unknown figure is not an all-clear.
    """
    if counts.count is None:
        return Health(HealthState.UNKNOWN, Movement.UNKNOWN, NOT_REPORTED)
    values, past = series_from_trend(trend) if trend else series_from_counts(counts)
    return assess_monitor(values, past, monitor.health)


def counts_for(summary: Summary, monitor: Monitor) -> MonitorCount:
    """The summary's figures for a monitor, or zeros for one the API does not report yet."""
    return summary.count_for(monitor.id) or MonitorCount(
        monitor_id=monitor.id, count=0, past_threshold_count=0
    )


def tile_state(monitor: Monitor, health: Health) -> Tone:
    """The health tone, except that an informational monitor never colours amber.

    Its incidents are context rather than a queue to work through, so it stays grey until
    the arithmetic actually calls it critical.
    """
    if health.state is HealthState.WARNING and monitor.severity is Severity.INFORMATIONAL:
        return Tone.GREY
    return health.tone


def tile_label(monitor: Monitor, health: Health) -> str:
    if health.state is HealthState.WARNING and monitor.severity is Severity.INFORMATIONAL:
        return INFO_LABEL
    return health.label


def tile_note(monitor: Monitor, count: int | None, past_threshold_count: int | None) -> str:
    if count is None:
        return NOT_REPORTED
    if count <= 0:
        return "no open incidents in this snapshot"
    if past_threshold_count is None:
        return f"{monitor.threshold_days}-day threshold count not reported"
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


def nav_badges(summary: Summary, trends: Trends | None = None) -> dict[str, NavBadge]:
    """Badge per sidebar item, keyed by monitor id plus `contact_queue`.

    A monitor with nothing open gets no badge, so the sidebar shows only what needs
    attention. Tone comes from the same assessment as the tile, over the same series, so the
    sidebar and the overview never disagree.
    """
    badges: dict[str, NavBadge] = {}
    if summary.past_threshold is not None and summary.past_threshold > 0:
        badges["contact_queue"] = NavBadge(str(summary.past_threshold), Tone.RED)
    for monitor in MONITOR_REGISTRY:
        counts = summary.count_for(monitor.id)
        if counts is None or counts.count is None or counts.count <= 0:
            continue
        health = monitor_health(monitor, counts, (trends or {}).get(monitor.id, ()))
        badges[monitor.id] = NavBadge(str(counts.count), tile_state(monitor, health))
    return badges


@dataclass(frozen=True, slots=True)
class Tile:
    monitor: Monitor
    count: int | None
    past_threshold_count: int | None
    health: Health
    state: Tone
    state_label: str
    note: str
    sparkline: tuple[float, ...]

    @property
    def value(self) -> str:
        return format_count(self.count)

    @property
    def trend_note(self) -> str:
        """The card's trend line: which way the series is moving, and how fast.

        Empty for a monitor with no history at all — a single point says nothing the count
        line above it has not already said, and the card is better without the row.
        """
        return self.health.headline if self.health.points >= MIN_TREND_POINTS else ""


def build_tiles(summary: Summary, trends: Trends | None = None) -> tuple[Tile, ...]:
    """One tile per registered monitor, in registry order.

    A monitor the API does not yet report is shown at zero rather than hidden, so a registry
    entry landing before its API endpoint is visible instead of silently missing. `trends`
    supplies the daily series the state is judged on; without it each monitor is judged on
    the sparkline in `/summary`.
    """
    tiles = []
    for monitor in MONITOR_REGISTRY:
        counts = counts_for(summary, monitor)
        health = monitor_health(monitor, counts, (trends or {}).get(monitor.id, ()))
        # Nulls are dropped rather than drawn as zeros, as on an incident's row sparkline.
        sparkline = tuple(p for p in counts.sparkline if p is not None)
        tiles.append(
            Tile(
                monitor=monitor,
                count=counts.count,
                past_threshold_count=counts.past_threshold_count,
                health=health,
                state=tile_state(monitor, health),
                state_label=tile_label(monitor, health),
                note=tile_note(monitor, counts.count, counts.past_threshold_count),
                sparkline=sparkline,
            )
        )
    return tuple(tiles)


def sidebar_counts(summary: Summary) -> dict[str, int]:
    """Monitor id -> open incident count, for the navigation labels.

    A monitor whose count the API did not report is left out: there is no number to show.
    """
    return {m.monitor_id: m.count for m in summary.monitors if m.count is not None}
