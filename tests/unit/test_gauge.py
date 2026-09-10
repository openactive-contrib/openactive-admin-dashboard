"""The benchmark arithmetic: the other thing most likely to be quietly wrong.

Every band is asserted at its boundary, because "just above the benchmark" and "just below"
are the two readings the card exists to distinguish.
"""

from __future__ import annotations

from typing import Any

import pytest

from stewards.monitors.gauge import (
    METER_HEIGHT,
    TRACK_BENCHMARKS,
    assess_benchmark,
    benchmark_phrase,
    benchmark_ratio,
    gauge_caption,
    gauge_chart,
    gauge_fraction,
    track_span,
)
from stewards.monitors.health import HealthState, Movement
from stewards.monitors.thresholds import Tone
from stewards.monitors.tile_viz import Gauge

SPEC = Gauge(benchmark=785_000)
COLOURS = {
    "value_colour": "#C77F1A",
    "track_colour": "#F1F4F5",
    "benchmark_colour": "#4A5A65",
}


def layer_value(spec: dict[str, Any], index: int) -> float:
    """The number one layer plots. Altair hoists inline data into named `datasets`."""
    layer = spec["layer"][index]
    values = spec["datasets"][layer["data"]["name"]]
    return float(values[0]["v"])


# --- the spec itself ----------------------------------------------------------------------


def test_a_benchmark_must_be_positive() -> None:
    """Zero would divide, and a negative one has no meaning on a track that starts at zero."""
    with pytest.raises(ValueError, match="positive"):
        Gauge(benchmark=0)
    with pytest.raises(ValueError, match="positive"):
        Gauge(benchmark=-1)


def test_the_bands_must_be_in_order() -> None:
    with pytest.raises(ValueError, match="warn_ratio"):
        Gauge(benchmark=100, warn_ratio=2.0, critical_ratio=1.0)
    assert Gauge(benchmark=100, warn_ratio=1.0, critical_ratio=1.0).warn_ratio == 1.0


# --- the geometry -------------------------------------------------------------------------


def test_the_track_runs_to_twice_the_benchmark() -> None:
    assert track_span(SPEC) == TRACK_BENCHMARKS * 785_000
    assert track_span(Gauge(benchmark=1)) == 2.0


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, 0.0),
        (392_500, 0.25),
        # The benchmark is the midpoint. This is the whole point of the visualisation.
        (785_000, 0.5),
        (1_570_000, 1.0),
        # Clamped, so a runaway figure fills the track rather than overflowing it.
        (3_000_000, 1.0),
    ],
)
def test_the_benchmark_sits_at_the_midpoint(count: int, expected: float) -> None:
    assert gauge_fraction(count, SPEC) == pytest.approx(expected)


def test_an_unreported_figure_has_no_position() -> None:
    assert gauge_fraction(None, SPEC) is None
    assert benchmark_ratio(None, SPEC) is None


def test_the_ratio_is_the_multiple_of_the_benchmark() -> None:
    assert benchmark_ratio(785_000, SPEC) == 1.0
    assert benchmark_ratio(1_570_000, SPEC) == 2.0
    assert benchmark_ratio(0, SPEC) == 0.0


# --- the state ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "state", "tone"),
    [
        (0, HealthState.HEALTHY, Tone.GREEN),
        (700_000, HealthState.HEALTHY, Tone.GREEN),
        # At warn_ratio exactly: still healthy, the band opens above it.
        (745_750, HealthState.HEALTHY, Tone.GREEN),
        (745_751, HealthState.WARNING, Tone.AMBER),
        (785_000, HealthState.WARNING, Tone.AMBER),
        # At critical_ratio exactly: still warning, on the same inclusive-below rule.
        (863_500, HealthState.WARNING, Tone.AMBER),
        (863_501, HealthState.CRITICAL, Tone.RED),
        (2_000_000, HealthState.CRITICAL, Tone.RED),
    ],
)
def test_each_band_is_decided_at_its_boundary(
    count: int, state: HealthState, tone: Tone
) -> None:
    health = assess_benchmark(count, SPEC)
    assert health.state is state
    assert health.tone is tone


def test_a_figure_the_snapshot_did_not_report_is_no_data_not_an_all_clear() -> None:
    health = assess_benchmark(None, SPEC)
    assert health.state is HealthState.UNKNOWN
    assert health.tone is Tone.GREY
    assert health.current is None
    assert health.baseline == 785_000
    assert "not reported" in health.reason


def test_the_benchmark_verdict_claims_no_movement() -> None:
    """One number carries a level and says nothing about direction."""
    for count in (0, 785_000, 2_000_000, None):
        health = assess_benchmark(count, SPEC)
        assert health.movement is Movement.UNKNOWN
        # Points stay at zero, so the card renders no trend line it cannot support.
        assert health.points == 0
        assert health.headline == "No trend reported"


def test_the_verdict_records_the_figure_and_the_benchmark_it_was_judged_against() -> None:
    health = assess_benchmark(784_293, SPEC)
    assert health.current == 784_293.0
    assert health.baseline == 785_000


# --- the words ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (785_000, "785,000 · level with the 785,000 benchmark"),
        (784_293, "784,293 · 0.1% below the 785,000 benchmark"),
        (863_500, "863,500 · 10.0% above the 785,000 benchmark"),
        (0, "0 · 100.0% below the 785,000 benchmark"),
        (None, "not reported this snapshot · benchmark 785,000"),
    ],
)
def test_the_phrase_states_the_figure_the_benchmark_and_the_gap(
    count: int | None, expected: str
) -> None:
    assert benchmark_phrase(count, SPEC) == expected
    assert gauge_caption(count, SPEC) == expected


def test_a_figure_within_rounding_of_the_benchmark_reads_as_level() -> None:
    """Not "0.0% above": a signed nothing invites a reader to see movement that is not there."""
    assert "level with" in benchmark_phrase(785_001, SPEC)
    assert "above" in benchmark_phrase(786_000, SPEC)


# --- the chart ----------------------------------------------------------------------------


def test_the_chart_layers_the_track_the_figure_and_the_benchmark() -> None:
    spec = gauge_chart(784_293, SPEC, **COLOURS).to_dict()
    assert len(spec["layer"]) == 3
    track, value, benchmark = spec["layer"]
    assert track["mark"]["color"] == "#F1F4F5"
    assert value["mark"]["color"] == "#C77F1A"
    assert benchmark["mark"]["type"] == "rule"
    assert benchmark["mark"]["color"] == "#4A5A65"
    assert spec["height"] == METER_HEIGHT
    # The three numbers the reader is being shown: the track, the figure, the benchmark.
    assert layer_value(spec, 0) == track_span(SPEC)
    assert layer_value(spec, 1) == 784_293
    assert layer_value(spec, 2) == 785_000
    # Every layer shares one scale, or position would not mean the same thing across them.
    domains = {tuple(layer["encoding"]["x"]["scale"]["domain"]) for layer in spec["layer"]}
    assert domains == {(0.0, track_span(SPEC))}


def test_the_value_bar_is_clamped_to_the_track() -> None:
    """Vega would happily draw past the axis; the caption carries the real multiple."""
    spec = gauge_chart(9_000_000, SPEC, **COLOURS).to_dict()
    assert layer_value(spec, 1) == track_span(SPEC)
    assert "103" not in str(spec)  # the real multiple lives in the caption, not the bar


def test_an_unreported_figure_draws_nothing_at_all() -> None:
    """The same contract as a too-short sparkline: None, not an empty axis."""
    assert gauge_chart(None, SPEC, **COLOURS) is None


def test_the_chart_height_is_the_callers_to_choose() -> None:
    spec = gauge_chart(1, SPEC, **COLOURS, height=80, bar_height=30).to_dict()
    assert spec["height"] == 80
    assert spec["layer"][0]["mark"]["height"] == 30
