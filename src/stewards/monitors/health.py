"""Trend-based health classification for a monitor's daily series.

A monitor card's state answers one question: is this monitor getting worse? The answer comes
from the monitor's own daily series rather than from a hand-set count threshold, so a monitor
whose numbers live in the hundreds and one that lives in single digits are judged on the same
footing.

Three signals, each chosen for the shape a daily batch series actually has — short (7 to 30
points), integer, heavily tied, occasionally spiky, sometimes with gaps:

* **Direction and speed** — the Theil-Sen slope, the median of the pairwise slopes between
  every pair of points. One outlier shifts a median by one rank rather than by its
  magnitude, which a least-squares fit cannot claim.
* **Confidence** — the Mann-Kendall S statistic with its tie correction, read as a two-sided
  p-value through the normal approximation. It tests monotonic movement without assuming a
  distribution, and ties (a plateau of identical counts) are what these series are made of.
* **Level** — the Iglewicz-Hoaglin modified z-score of the latest point against the recent
  median and its median absolute deviation. This catches a step change that a slope over the
  whole window has not had time to register.

Speed is reported relative to the series' own level, so `rate` is a fraction per day and is
comparable across monitors; `doubling_days` restates it in the unit a reader can act on. The
relative rate divides by `max(level, level_floor)`, so a series stepping 0 → 1 → 2 does not
read as +100%/day.

`Direction` is per monitor: for an incident count up is bad, and for a coverage or
opportunity count down is bad. Every rule below works on the *oriented* series (the raw value
times the direction's sign), so there is one code path and no branch on the direction in the
classification itself.

Streamlit-free and clock-free by design: everything here is a pure function of a sequence of
numbers, which is what `tests/unit/test_health.py` exercises.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from itertools import combinations
from math import erfc, inf, isfinite, log, sqrt
from statistics import fmean, median

from stewards.monitors.thresholds import Tone

#: A daily series as the API reports it: one point per snapshot, null where the batch had no
#: figure for that day. Nulls are holes in the series, never zeros.
Series = Sequence[float | None]


class Direction(StrEnum):
    """Which way is bad. An incident count rises when things get worse; a coverage or
    opportunity count falls."""

    UP_IS_BAD = "up_is_bad"
    DOWN_IS_BAD = "down_is_bad"

    @property
    def sign(self) -> int:
        """The multiplier that turns raw values into "higher is worse" values."""
        return 1 if self is Direction.UP_IS_BAD else -1


class HealthState(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    HEALTHY = "healthy"
    UNKNOWN = "unknown"


class Movement(StrEnum):
    """The trend verdict on its own, independent of the level."""

    WORSENING = "worsening"
    STEADY = "steady"
    IMPROVING = "improving"
    UNKNOWN = "unknown"


#: Ascending badness, for combining two assessments. An unknown figure ranks above healthy:
#: a series the batch did not report is not an all-clear.
_BADNESS = (HealthState.HEALTHY, HealthState.UNKNOWN, HealthState.WARNING, HealthState.CRITICAL)

TONES: dict[HealthState, Tone] = {
    HealthState.CRITICAL: Tone.RED,
    HealthState.WARNING: Tone.AMBER,
    HealthState.HEALTHY: Tone.GREEN,
    HealthState.UNKNOWN: Tone.GREY,
}

LABELS: dict[HealthState, str] = {
    HealthState.CRITICAL: "Critical",
    HealthState.WARNING: "Warning",
    HealthState.HEALTHY: "Healthy",
    HealthState.UNKNOWN: "No data",
}


def worse(first: HealthState, second: HealthState) -> HealthState:
    """The more severe of two states."""
    return max(first, second, key=_BADNESS.index)


@dataclass(frozen=True, slots=True)
class HealthPolicy:
    """What "getting worse" means for one monitor. Defaults suit a daily incident count.

    Every field is in units a reader can argue about: `warn_rate` and `critical_rate` are
    fractions of the series' own level per day (1%/day is roughly a third in a month; 5%/day
    doubles in a fortnight), and `max_p` is the two-sided p-value below which Mann-Kendall
    is taken to have found a real trend rather than noise.
    """

    direction: Direction = Direction.UP_IS_BAD

    #: The raw value at or beyond which — in the good direction — there is nothing to report:
    #: zero open incidents for a count. None means the monitor has no such point, which is
    #: the case for a volume figure: only its movement can be judged.
    clear_level: float | None = 0.0

    #: Points needed before a trend verdict is claimed at all. Below this the level still
    #: decides the state, and the movement reads as unknown.
    min_points: int = 5

    #: Points the slope and the significance test consider, newest last.
    window: int = 30

    #: Points the level baseline considers (the median and MAD the latest point is compared
    #: against, and the scale the slope is divided by).
    baseline_window: int = 14

    #: Floor under the divisor of the relative rate, and the smallest absolute step change
    #: that may escalate a state. Keeps single-digit noise from reading as a large move.
    level_floor: float = 3.0

    warn_rate: float = 0.01
    critical_rate: float = 0.05
    max_p: float = 0.10

    warn_z: float = 2.0
    critical_z: float = 3.5

    #: Whether a non-empty secondary series (the past-threshold subset) escalates the state.
    #: See `assess_monitor`.
    secondary_escalates: bool = True


DEFAULT_POLICY = HealthPolicy()


def secondary_policy(policy: HealthPolicy) -> HealthPolicy:
    """The past-threshold subset is a count of incidents whichever way the monitor's own
    figure points, so it is judged up-is-bad and clear at zero."""
    return replace(policy, direction=Direction.UP_IS_BAD, clear_level=0.0)


# --- the statistics -----------------------------------------------------------------------


def oriented_points(values: Series, direction: Direction) -> tuple[tuple[float, float], ...]:
    """`(day, oriented value)` for every reported point, higher meaning worse.

    The day is the point's position in the full series, so a null day leaves a gap in x
    rather than pulling the following points closer together and steepening the slope.
    """
    sign = direction.sign
    return tuple((float(i), sign * v) for i, v in enumerate(values) if v is not None)


def theil_sen_slope(points: Sequence[tuple[float, float]]) -> float:
    """Median of the pairwise slopes, in value units per day. Zero for fewer than 2 points."""
    slopes = [
        (y2 - y1) / (x2 - x1) for (x1, y1), (x2, y2) in combinations(points, 2) if x2 != x1
    ]
    return median(slopes) if slopes else 0.0


@dataclass(frozen=True, slots=True)
class TrendTest:
    """The Mann-Kendall result: S, Kendall's tau-b, and the two-sided p-value."""

    s: float = 0.0
    tau: float = 0.0
    p_value: float = 1.0


def mann_kendall(values: Sequence[float]) -> TrendTest:
    """Non-parametric test for monotonic trend, tie-corrected.

    S counts concordant minus discordant pairs; its variance carries the correction for tied
    values, which dominate these series. The normal approximation with a continuity
    correction gives the p-value — it is only asymptotic, which is why `min_points` keeps
    very short series from claiming a verdict.
    """
    n = len(values)
    if n < 3:
        return TrendTest()

    s = float(
        sum((second > first) - (second < first) for first, second in combinations(values, 2))
    )
    ties = [count for count in Counter(values).values() if count > 1]
    pairs = n * (n - 1) / 2
    tied_pairs = sum(t * (t - 1) / 2 for t in ties)
    variance = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in ties)) / 18.0

    denominator = sqrt((pairs - tied_pairs) * pairs)
    tau = s / denominator if denominator > 0 else 0.0
    if variance <= 0 or s == 0:
        return TrendTest(s, tau, 1.0)
    z = (abs(s) - 1) / sqrt(variance)
    return TrendTest(s, tau, erfc(z / sqrt(2)))


def modified_zscore(value: float, baseline: Sequence[float]) -> float:
    """How far the latest point sits outside its recent range, in robust deviations.

    Iglewicz-Hoaglin: `0.6745 * (x - median) / MAD`. Integer count series routinely have a
    zero MAD (a plateau of identical values), so the mean absolute deviation stands in with
    its own consistency constant; with no spread at all the point is either identical to the
    baseline (0) or unprecedented (infinite), and the caller's `level_floor` decides whether
    an unprecedented but tiny step is worth escalating.
    """
    if not baseline:
        return 0.0
    centre = median(baseline)
    deviation = value - centre
    if deviation == 0:
        return 0.0
    spread = median([abs(point - centre) for point in baseline])
    if spread > 0:
        return 0.6745 * deviation / spread
    mean_spread = fmean([abs(point - centre) for point in baseline])
    if mean_spread > 0:
        return 0.7979 * deviation / mean_spread
    return inf if deviation > 0 else -inf


def doubling_days(rate: float) -> float | None:
    """Days for a series moving at `rate` per day to double, or halve. None if it is flat."""
    if rate == 0 or not isfinite(rate):
        return None
    return log(2) / abs(rate)


# --- the verdict --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Health:
    """A monitor's state, and every number that decided it."""

    state: HealthState
    movement: Movement
    reason: str
    points: int = 0

    #: Signed change per day in the monitor's own units, as reported (not oriented): negative
    #: means the figure itself is falling, whichever direction is bad for this monitor.
    slope: float = 0.0

    #: Change per day as a fraction of the series' own level, oriented: positive is worse.
    rate: float = 0.0

    p_value: float = 1.0
    tau: float = 0.0

    #: Robust z-score of the latest point against the baseline, oriented: positive is worse.
    deviation: float = 0.0

    #: The latest reported value and the baseline median, both as reported.
    current: float | None = None
    baseline: float | None = None

    @property
    def tone(self) -> Tone:
        return TONES[self.state]

    @property
    def label(self) -> str:
        return LABELS[self.state]

    @property
    def doubling_days(self) -> float | None:
        return doubling_days(self.rate)

    @property
    def headline(self) -> str:
        """One line for the card: which way, how fast, over how much history."""
        if self.points == 0:
            return "No trend reported"
        if self.movement is Movement.UNKNOWN:
            return f"{_snapshots(self.points)}, too short to judge a trend"
        if self.movement is Movement.STEADY:
            return f"Steady across {_snapshots(self.points)}"
        direction = "Rising" if self.slope > 0 else "Falling"
        line = f"{direction} {_percent_per_day(self.rate)} across {_snapshots(self.points)}"
        days = self.doubling_days
        if days is not None and days <= 60:
            # The verb follows the figure itself, not the verdict: a monitor whose volume is
            # falling is halving, however bad that is for it.
            verb = "doubling" if self.slope > 0 else "halving"
            line = f"{line}, {verb} in {days:.0f} days"
        return line


def _snapshots(count: int) -> str:
    return "1 snapshot" if count == 1 else f"{count} snapshots"


def _percent_per_day(rate: float) -> str:
    magnitude = abs(rate) * 100
    if magnitude < 1:
        return "under 1%/day"
    return f"{magnitude:.0f}%/day"


_NO_SERIES = "no series reported for this monitor"


def _steady_reason(movement: Movement) -> str:
    """Why a monitor with no clear level is healthy: it is not moving fast enough to flag."""
    if movement is Movement.WORSENING:
        return "moving in the wrong direction, but below the warning rate"
    if movement is Movement.IMPROVING:
        return "moving in the right direction"
    return "no significant movement"


def assess(values: Series, policy: HealthPolicy = DEFAULT_POLICY) -> Health:
    """Classify one daily series.

    Order of the rules, most severe first: a series at or beyond its clear level is healthy
    whatever it did on the way there — nothing is open now. Otherwise a significant trend in
    the bad direction, or a step change against the recent range, sets the state; a level
    above the clear point with no significant movement is a warning, and a monitor with no
    clear level and no movement is healthy.
    """
    points = oriented_points(values, policy.direction)
    if not points:
        return Health(HealthState.UNKNOWN, Movement.UNKNOWN, _NO_SERIES)

    sign = policy.direction.sign
    window = points[-policy.window :]
    series = [value for _, value in window]
    current = series[-1]
    baseline = series[-(policy.baseline_window + 1) : -1]
    centre = median(baseline) if baseline else current
    scale = median([abs(value) for value in series[-policy.baseline_window :]])

    slope = theil_sen_slope(window)
    test = mann_kendall(series)
    rate = slope / max(scale, policy.level_floor)
    if len(window) < policy.min_points:
        movement = Movement.UNKNOWN
    elif test.p_value <= policy.max_p and slope > 0:
        movement = Movement.WORSENING
    elif test.p_value <= policy.max_p and slope < 0:
        movement = Movement.IMPROVING
    else:
        movement = Movement.STEADY

    deviation = modified_zscore(current, baseline)
    # A step change must be worth something in absolute terms as well: on a series that has
    # sat at zero all month, one incident is unprecedented but not a story.
    stepped = current - centre >= policy.level_floor
    clear = None if policy.clear_level is None else sign * policy.clear_level
    worsening = movement is Movement.WORSENING

    if clear is not None and current <= clear:
        state, reason = HealthState.HEALTHY, "at or better than the clear level"
    elif worsening and rate >= policy.critical_rate:
        state, reason = HealthState.CRITICAL, "sustained movement in the wrong direction"
    elif stepped and deviation >= policy.critical_z:
        state, reason = HealthState.CRITICAL, "step change against the recent range"
    elif worsening and rate >= policy.warn_rate:
        state, reason = HealthState.WARNING, "moving in the wrong direction"
    elif stepped and deviation >= policy.warn_z:
        state, reason = HealthState.WARNING, "outside its recent range"
    elif clear is None:
        state, reason = HealthState.HEALTHY, _steady_reason(movement)
    elif movement is Movement.IMPROVING:
        state, reason = HealthState.WARNING, "above the clear level, improving"
    else:
        state, reason = HealthState.WARNING, "above the clear level"

    return Health(
        state=state,
        movement=movement,
        reason=reason,
        points=len(window),
        slope=sign * slope,
        rate=rate,
        p_value=test.p_value,
        tau=test.tau,
        deviation=deviation,
        current=sign * current,
        baseline=sign * centre,
    )


def assess_monitor(
    values: Series,
    past_threshold: Series = (),
    policy: HealthPolicy = DEFAULT_POLICY,
) -> Health:
    """Classify a monitor from its own series plus the past-threshold subset.

    The subset is the more serious figure — those are the publishers waiting to be contacted
    — so a non-empty one escalates the state to critical, unless the backlog is being worked
    down, in which case a warning is the honest reading. "Worked down" means both trending
    down and below its own recent range: a backlog that fell all month and jumped back today
    is not recovering.

    The subset only ever escalates: an empty or unreported one leaves the monitor's own
    verdict alone. The verdict returned is that own trend, restated with whichever state is
    the more severe of the two, so the card's caption still describes the figure the card
    shows.
    """
    health = assess(values, policy)
    if not policy.secondary_escalates:
        return health

    # The subset can only escalate: an empty or unreported one says nothing, and whether the
    # monitor itself is unknown was already settled by its own series.
    subset = assess(past_threshold, secondary_policy(policy))
    if subset.state is HealthState.HEALTHY or subset.state is HealthState.UNKNOWN:
        return health

    # A backlog only counts as being worked down if it is below its own recent range as well
    # as trending down: one that fell all month and jumped back today is not recovering.
    recovering = subset.movement is Movement.IMPROVING and subset.deviation <= 0
    escalated = HealthState.WARNING if recovering else HealthState.CRITICAL
    if _BADNESS.index(escalated) < _BADNESS.index(health.state):
        return health
    return replace(health, state=worse(health.state, escalated), reason=_subset_reason(subset))


def _subset_reason(subset: Health) -> str:
    """Why the past-threshold subset set the state."""
    if subset.movement is Movement.WORSENING:
        return "past the contact threshold and rising"
    if subset.deviation > 0:
        return "past the contact threshold, up on its recent range"
    if subset.movement is Movement.IMPROVING:
        return "past the contact threshold and falling"
    return "past the contact threshold and not falling"
