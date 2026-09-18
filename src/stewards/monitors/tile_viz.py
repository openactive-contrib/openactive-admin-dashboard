"""How a monitor's overview card draws its figure.

One variant per shape of evidence. A monitor with a daily series gets `Sparkline`; a monitor
that has only this snapshot's number gets `Gauge`, which reads it against a fixed benchmark
instead of against its own history; a monitor whose snapshot is a set of figures rather than
one number gets `Facts`. Switching a card is one line in the registry entry.

Pure declarations: the arithmetic lives in `monitors.gauge` and `monitors.trend`, and the
colours are chosen by the component that draws them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Sparkline:
    """The monitor's own daily series, drawn axis-less. The default for every monitor."""


@dataclass(frozen=True, slots=True)
class Gauge:
    """This snapshot's figure against a fixed benchmark, for a monitor with no history.

    The benchmark sits at the midpoint of the track, so a figure at the benchmark reads as
    half full and one that has doubled fills it. `warn_ratio` and `critical_ratio` are
    multiples of the benchmark, and they set the card's state as well as its colour: with no
    series to judge, the benchmark is the only reference the monitor has.
    """

    benchmark: float
    warn_ratio: float = 0.95
    critical_ratio: float = 1.10

    def __post_init__(self) -> None:
        if self.benchmark <= 0:
            raise ValueError("a gauge benchmark must be positive")
        if self.warn_ratio > self.critical_ratio:
            raise ValueError("warn_ratio must not exceed critical_ratio")


@dataclass(frozen=True, slots=True)
class Facts:
    """A headline figure and a few supporting ones, for a monitor whose card summarises.

    A quality snapshot has no single count worth a card: "73 average score" alone says
    nothing about how many feeds carry an error. The card therefore draws no chart and the
    monitor supplies the figures itself, as an `overview.TileCard` — including its own
    `Health`, so the chip, the tone and the sidebar pill still run through the code every
    other monitor uses.
    """


#: Every variant a monitor may declare. Adding one is a dataclass here, a builder beside
#: `monitors.gauge.gauge_chart`, and one `case` in `components.overview_page.tile_chart`.
TileViz = Sparkline | Gauge | Facts
