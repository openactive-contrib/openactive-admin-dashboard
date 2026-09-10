"""The trend arithmetic behind a monitor card's state.

This is the module most likely to be quietly wrong — a slope, a p-value and a robust
z-score all have boundaries that are easy to get one step out — so every rule is asserted
at its boundary, and the direction parameter is asserted to be a mirror rather than a
special case.
"""

from __future__ import annotations

from dataclasses import replace
from math import inf, isclose, log

import pytest

from stewards.monitors.health import (
    DEFAULT_POLICY,
    Direction,
    Health,
    HealthPolicy,
    HealthState,
    Movement,
    assess,
    assess_monitor,
    doubling_days,
    mann_kendall,
    modified_zscore,
    oriented_points,
    secondary_policy,
    theil_sen_slope,
    worse,
)
from stewards.monitors.thresholds import Tone

RISING = [10.0, 11, 12, 13, 14, 15, 16, 17, 18, 19]
FLAT = [15.0] * 20
ZERO = [0.0] * 20

#: The relative rate of RISING under the default baseline window: slope 1.0 a day over a
#: median level of 14.5. Both boundary tests below are pinned to it.
RISING_RATE = 1 / 14.5


# --- orientation --------------------------------------------------------------------------


def test_up_is_bad_leaves_the_series_alone() -> None:
    assert oriented_points([1.0, 2.0], Direction.UP_IS_BAD) == ((0.0, 1.0), (1.0, 2.0))


def test_down_is_bad_flips_the_series() -> None:
    assert oriented_points([1.0, 2.0], Direction.DOWN_IS_BAD) == ((0.0, -1.0), (1.0, -2.0))


def test_an_empty_series_has_no_points() -> None:
    assert oriented_points([], Direction.UP_IS_BAD) == ()
    assert oriented_points([None, None], Direction.UP_IS_BAD) == ()


def test_a_null_leaves_a_gap_in_the_days_rather_than_closing_up() -> None:
    """Closing the gap would steepen the slope across a day the batch did not report."""
    assert oriented_points([1.0, None, 3.0], Direction.UP_IS_BAD) == ((0.0, 1.0), (2.0, 3.0))


# --- Theil-Sen ----------------------------------------------------------------------------


def test_slope_of_a_straight_line_is_its_gradient() -> None:
    assert theil_sen_slope([(0.0, 0.0), (1.0, 2.0), (2.0, 4.0)]) == 2.0


def test_slope_of_an_empty_or_single_point_series_is_zero() -> None:
    assert theil_sen_slope([]) == 0.0
    assert theil_sen_slope([(0.0, 7.0)]) == 0.0


def test_slope_of_a_flat_series_is_zero() -> None:
    assert theil_sen_slope([(float(i), 15.0) for i in range(10)]) == 0.0


def test_one_outlier_does_not_carry_the_slope() -> None:
    """The point of a median of pairwise slopes: a spike moves it by one rank, not by 100."""
    flat = [(float(i), 4.0) for i in range(10)]
    assert theil_sen_slope([*flat, (10.0, 104.0)]) == 0.0


def test_slope_respects_the_gap_a_null_day_leaves() -> None:
    assert theil_sen_slope([(0.0, 0.0), (10.0, 10.0)]) == 1.0


# --- Mann-Kendall -------------------------------------------------------------------------


def test_a_series_too_short_to_test_claims_nothing() -> None:
    result = mann_kendall([1.0, 2.0])
    assert (result.s, result.tau, result.p_value) == (0.0, 0.0, 1.0)


def test_a_monotonic_rise_is_significant_with_tau_one() -> None:
    result = mann_kendall(RISING)
    assert result.tau == 1.0
    assert result.p_value < 0.01
    assert result.s > 0


def test_a_monotonic_fall_is_significant_with_tau_minus_one() -> None:
    result = mann_kendall(list(reversed(RISING)))
    assert result.tau == -1.0
    assert result.p_value < 0.01
    assert result.s < 0


def test_a_wholly_tied_series_carries_no_trend() -> None:
    """The tie correction is what keeps a plateau from reading as a trend."""
    result = mann_kendall(FLAT)
    assert (result.s, result.tau, result.p_value) == (0.0, 0.0, 1.0)


def test_noise_around_a_level_is_not_significant() -> None:
    noisy = [12.0, 15, 11, 14, 13, 12, 16, 11, 13, 14, 12, 15, 13, 12]
    assert mann_kendall(noisy).p_value > 0.10


def test_ties_lower_the_confidence_of_the_same_direction() -> None:
    clean = mann_kendall([1.0, 2, 3, 4, 5, 6, 7, 8])
    tied = mann_kendall([1.0, 1, 2, 2, 3, 3, 4, 4])
    assert clean.p_value < tied.p_value


# --- the robust z-score -------------------------------------------------------------------


def test_zscore_of_an_empty_baseline_is_zero() -> None:
    assert modified_zscore(9.0, []) == 0.0


def test_a_point_on_the_baseline_median_has_no_deviation() -> None:
    assert modified_zscore(10.0, [9.0, 10.0, 11.0]) == 0.0


def test_zscore_uses_the_median_absolute_deviation() -> None:
    assert isclose(modified_zscore(16.0, [9.0, 10.0, 11.0] * 4), 0.6745 * 6 / 1)


def test_a_tied_baseline_falls_back_to_the_mean_deviation() -> None:
    """Integer counts routinely have a zero MAD, which would otherwise divide by nothing."""
    baseline = [0.0, 0, 0, 1, 0, 0, 0, 1, 0, 0]
    assert isclose(modified_zscore(2.0, baseline), 0.7979 * 2 / 0.2)


def test_an_unprecedented_point_against_a_flat_baseline_is_infinite() -> None:
    assert modified_zscore(1.0, ZERO) == inf
    assert modified_zscore(-1.0, ZERO) == -inf


# --- rate arithmetic ----------------------------------------------------------------------


def test_doubling_days_from_a_rate() -> None:
    assert isclose(doubling_days(0.10) or 0.0, log(2) / 0.10)


def test_a_flat_or_infinite_rate_has_no_doubling_time() -> None:
    assert doubling_days(0.0) is None
    assert doubling_days(inf) is None


def test_halving_is_the_same_arithmetic_as_doubling() -> None:
    assert doubling_days(-0.10) == doubling_days(0.10)


# --- states -------------------------------------------------------------------------------


def test_an_unreported_series_is_unknown_not_healthy() -> None:
    health = assess([None, None, None])
    assert health.state is HealthState.UNKNOWN
    assert health.movement is Movement.UNKNOWN
    assert health.tone is Tone.GREY
    assert health.points == 0
    assert health.headline == "No trend reported"


def test_nothing_open_is_healthy_however_it_got_there() -> None:
    health = assess([30.0, 20, 12, 6, 2, 0])
    assert health.state is HealthState.HEALTHY
    assert health.tone is Tone.GREEN
    assert health.reason == "at or better than the clear level"


def test_something_open_and_steady_is_a_warning() -> None:
    health = assess(FLAT)
    assert health.state is HealthState.WARNING
    assert health.movement is Movement.STEADY
    assert health.tone is Tone.AMBER
    assert health.headline == "Steady across 20 snapshots"


def test_a_sustained_rise_is_critical() -> None:
    health = assess([2.0, 2, 3, 4, 4, 6, 8, 9, 12, 16, 18, 24, 32, 36])
    assert health.state is HealthState.CRITICAL
    assert health.movement is Movement.WORSENING
    assert health.tone is Tone.RED
    assert health.reason == "sustained movement in the wrong direction"
    assert "doubling in" in health.headline


def test_a_backlog_being_worked_down_is_a_warning_not_a_crisis() -> None:
    health = assess(list(range(30, 3, -1)))
    assert health.state is HealthState.WARNING
    assert health.movement is Movement.IMPROVING
    assert health.reason == "above the clear level, improving"
    assert "halving in" in health.headline


def test_a_step_change_is_critical_before_any_trend_can_form() -> None:
    health = assess([4.0] * 20 + [22.0])
    assert health.state is HealthState.CRITICAL
    assert health.movement is Movement.STEADY
    assert health.reason == "step change against the recent range"


def test_a_series_shorter_than_min_points_is_judged_on_its_level_alone() -> None:
    health = assess([5.0, 6, 7])
    assert health.movement is Movement.UNKNOWN
    assert health.state is HealthState.WARNING
    assert health.headline == "3 snapshots, too short to judge a trend"


def test_min_points_is_the_boundary_at_which_a_trend_may_be_claimed() -> None:
    series = [10.0, 12, 14, 16, 18]
    assert assess(series, HealthPolicy(min_points=5)).movement is Movement.WORSENING
    assert assess(series, HealthPolicy(min_points=6)).movement is Movement.UNKNOWN


def test_a_single_point_is_a_level_with_no_trend() -> None:
    assert assess([4.0]).state is HealthState.WARNING
    assert assess([0.0]).state is HealthState.HEALTHY
    assert assess([4.0]).headline == "1 snapshot, too short to judge a trend"


def test_the_critical_rate_is_inclusive_at_its_boundary() -> None:
    """`rate == critical_rate` is critical; a hair above the rate is only a warning."""
    at = assess(RISING, HealthPolicy(critical_rate=RISING_RATE))
    above = assess(RISING, HealthPolicy(critical_rate=RISING_RATE + 1e-9))
    assert isclose(at.rate, RISING_RATE)
    assert at.state is HealthState.CRITICAL
    assert above.state is HealthState.WARNING


def test_the_warn_rate_is_inclusive_at_its_boundary() -> None:
    policy = HealthPolicy(critical_rate=1.0, warn_rate=RISING_RATE, clear_level=None)
    above = replace(policy, warn_rate=RISING_RATE + 1e-9)
    assert assess(RISING, policy).state is HealthState.WARNING
    assert assess(RISING, above).state is HealthState.HEALTHY


def test_movement_needs_the_p_value_as_well_as_the_slope() -> None:
    """A rise the test cannot separate from noise is steady, whatever the slope says."""
    sawtooth = [10.0, 15, 11, 16, 12, 17, 13, 18, 14, 19]
    assert assess(sawtooth, HealthPolicy(max_p=0.001)).movement is Movement.STEADY
    assert assess(sawtooth, HealthPolicy(max_p=0.50)).movement is Movement.WORSENING


def test_a_small_absolute_step_does_not_escalate_however_unprecedented() -> None:
    """One incident after a quiet month is unprecedented, and still not a story."""
    health = assess([*ZERO, 1.0], HealthPolicy(clear_level=None))
    assert health.deviation == inf
    assert health.state is HealthState.HEALTHY


def test_the_level_floor_is_the_boundary_of_an_escalating_step() -> None:
    baseline = [9.0, 10, 11] * 4
    policy = HealthPolicy(clear_level=None)
    assert assess([*baseline, 13.0], policy).state is HealthState.WARNING
    assert assess([*baseline, 12.0], policy).state is HealthState.HEALTHY


def test_the_relative_rate_is_scale_free() -> None:
    small = assess([4.0, 5, 6, 7, 8, 9, 10, 11])
    large = assess([400.0, 500, 600, 700, 800, 900, 1000, 1100])
    assert isclose(small.rate, large.rate)
    assert small.state is large.state is HealthState.CRITICAL


# --- direction ----------------------------------------------------------------------------

FALLING_VOLUME = [round(400_000 * 0.95**i) for i in range(30)]
VOLUME_POLICY = HealthPolicy(
    direction=Direction.DOWN_IS_BAD, clear_level=None, secondary_escalates=False
)


def test_a_falling_volume_is_critical_when_down_is_bad() -> None:
    health = assess(FALLING_VOLUME, VOLUME_POLICY)
    assert health.state is HealthState.CRITICAL
    assert health.movement is Movement.WORSENING
    assert health.slope < 0
    assert health.rate > 0
    assert health.headline.startswith("Falling")
    assert "halving in" in health.headline


def test_the_same_series_is_healthy_when_up_is_bad() -> None:
    up_is_bad = replace(VOLUME_POLICY, direction=Direction.UP_IS_BAD)
    health = assess(FALLING_VOLUME, up_is_bad)
    assert health.state is HealthState.HEALTHY
    assert health.movement is Movement.IMPROVING
    assert health.reason == "moving in the right direction"


def test_direction_is_a_mirror_and_not_a_special_case() -> None:
    rising = assess(RISING)
    down = HealthPolicy(direction=Direction.DOWN_IS_BAD)
    mirrored = assess([-value for value in RISING], down)
    assert mirrored.state is rising.state
    assert mirrored.movement is rising.movement
    assert isclose(mirrored.rate, rising.rate)
    assert isclose(mirrored.slope, -rising.slope)


def test_a_volume_with_no_clear_level_is_healthy_while_it_holds() -> None:
    health = assess([400_000.0 + (i % 3) * 500 for i in range(30)], VOLUME_POLICY)
    assert health.state is HealthState.HEALTHY
    assert health.reason == "no significant movement"


def test_movement_below_the_warning_rate_is_healthy_but_says_so() -> None:
    creeping = [round(400_000 * 0.995**i) for i in range(30)]
    health = assess(creeping, VOLUME_POLICY)
    assert health.state is HealthState.HEALTHY
    assert health.movement is Movement.WORSENING
    assert health.reason == "moving in the wrong direction, but below the warning rate"


def test_the_direction_sign_is_the_whole_of_the_parameter() -> None:
    assert Direction.UP_IS_BAD.sign == 1
    assert Direction.DOWN_IS_BAD.sign == -1


# --- the past-threshold subset ------------------------------------------------------------


def test_an_empty_subset_leaves_the_verdict_alone() -> None:
    own = assess(FLAT)
    assert assess_monitor(FLAT, ()) == own
    assert assess_monitor(FLAT, ZERO).state is own.state


def test_a_standing_backlog_is_critical() -> None:
    health = assess_monitor(FLAT, [3.0] * 20)
    assert health.state is HealthState.CRITICAL
    assert health.reason == "past the contact threshold and not falling"
    # The caption still describes the monitor's own figure, not the subset's.
    assert health.headline == "Steady across 20 snapshots"


def test_a_rising_backlog_says_so() -> None:
    health = assess_monitor(FLAT, [float(i) for i in range(20)])
    assert health.state is HealthState.CRITICAL
    assert health.reason == "past the contact threshold and rising"


def test_a_backlog_being_worked_down_is_only_a_warning() -> None:
    health = assess_monitor(FLAT, [12.0, 11, 10, 9, 8, 7, 6, 5, 4, 3])
    assert health.state is HealthState.WARNING
    assert health.reason == "past the contact threshold and falling"


def test_a_backlog_that_steps_back_up_is_critical_whatever_its_longer_trend_did() -> None:
    """A month of clearing the queue does not excuse seven publishers waiting today."""
    subset = [7.0, 7, 6, 6, 5, 5, 4, 4, 3, 3, 2, 2, 1, 1, 0, 0, 0, 0, 0, 7]
    health = assess_monitor(FLAT, subset)
    assert health.state is HealthState.CRITICAL
    assert health.reason == "past the contact threshold, up on its recent range"


def test_a_cleared_backlog_does_not_hold_the_monitor_red() -> None:
    health = assess_monitor(FLAT, [4.0] * 19 + [0.0])
    assert health.state is HealthState.WARNING


def test_an_unreported_subset_leaves_the_monitors_own_verdict_alone() -> None:
    """The subset can only escalate. Whether the monitor itself is unknown is settled by its
    own series, which `monitors.overview` reads from the reported count."""
    assert assess_monitor(ZERO, [None] * 20).state is HealthState.HEALTHY
    assert assess_monitor(FLAT, [None] * 20).state is HealthState.WARNING


def test_a_monitor_can_opt_out_of_subset_escalation() -> None:
    policy = HealthPolicy(secondary_escalates=False)
    assert assess_monitor(FLAT, [9.0] * 20, policy).state is HealthState.WARNING


def test_the_subset_is_always_judged_up_is_bad() -> None:
    """A volume monitor's own figure may fall when things go wrong; its backlog still rises."""
    policy = secondary_policy(HealthPolicy(direction=Direction.DOWN_IS_BAD, clear_level=None))
    assert policy.direction is Direction.UP_IS_BAD
    assert policy.clear_level == 0.0


def test_a_critical_monitor_is_not_downgraded_by_a_falling_backlog() -> None:
    rising = [2.0, 2, 3, 4, 4, 6, 8, 9, 12, 16, 18, 24, 32, 36]
    health = assess_monitor(rising, [9.0, 8, 7, 6, 5, 4, 3, 2, 1, 1])
    assert health.state is HealthState.CRITICAL
    assert health.reason == "sustained movement in the wrong direction"


# --- presentation -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "tone", "label"),
    [
        (HealthState.CRITICAL, Tone.RED, "Critical"),
        (HealthState.WARNING, Tone.AMBER, "Warning"),
        (HealthState.HEALTHY, Tone.GREEN, "Healthy"),
        (HealthState.UNKNOWN, Tone.GREY, "No data"),
    ],
)
def test_every_state_has_a_tone_and_a_label(state: HealthState, tone: Tone, label: str) -> None:
    health = Health(state, Movement.STEADY, "because")
    assert health.tone is tone
    assert health.label == label


def test_a_rate_under_one_percent_is_not_rounded_to_zero() -> None:
    health = assess([round(400_000 * 0.995**i) for i in range(30)], VOLUME_POLICY)
    assert "under 1%/day" in health.headline


def test_a_slow_trend_carries_no_doubling_clause() -> None:
    """Past two months out, a doubling time is arithmetic rather than information."""
    health = assess([100.0 + i * 0.5 for i in range(30)], HealthPolicy(clear_level=None))
    assert health.movement is Movement.WORSENING
    assert "doubling" not in health.headline


def test_the_default_policy_is_an_up_is_bad_incident_count() -> None:
    assert DEFAULT_POLICY.direction is Direction.UP_IS_BAD
    assert DEFAULT_POLICY.clear_level == 0.0


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (HealthState.HEALTHY, HealthState.CRITICAL, HealthState.CRITICAL),
        (HealthState.WARNING, HealthState.CRITICAL, HealthState.CRITICAL),
        (HealthState.HEALTHY, HealthState.UNKNOWN, HealthState.UNKNOWN),
        (HealthState.WARNING, HealthState.UNKNOWN, HealthState.WARNING),
        (HealthState.HEALTHY, HealthState.HEALTHY, HealthState.HEALTHY),
    ],
)
def test_worse_ranks_an_unknown_figure_above_an_all_clear(
    first: HealthState, second: HealthState, expected: HealthState
) -> None:
    assert worse(first, second) is expected
    assert worse(second, first) is expected
