"""Every registry entry declares a visualisation the overview knows how to draw.

Parametrised over the whole registry, so a future monitor is validated for free — the same
bargain `test_registry.py` makes.
"""

from __future__ import annotations

import pytest

from stewards.monitors.registry import MONITOR_REGISTRY, Monitor
from stewards.monitors.tile_viz import Gauge, Sparkline, TileViz

pytestmark = pytest.mark.parametrize(
    "monitor", MONITOR_REGISTRY, ids=[m.id for m in MONITOR_REGISTRY]
)


def test_the_declared_visualisation_is_one_the_overview_can_draw(monitor: Monitor) -> None:
    assert isinstance(monitor.viz, Sparkline | Gauge)


def test_a_sparkline_is_the_default(monitor: Monitor) -> None:
    """Switching a card is one line, and adding a monitor needs no line at all."""
    assert isinstance(Monitor.__dataclass_fields__["viz"].default_factory(), Sparkline)


def test_a_gauge_declares_a_positive_benchmark_and_ordered_bands(monitor: Monitor) -> None:
    if not isinstance(monitor.viz, Gauge):
        pytest.skip(f"{monitor.id} is judged on its own series")
    assert monitor.viz.benchmark > 0
    assert monitor.viz.warn_ratio <= monitor.viz.critical_ratio


def test_a_gauge_monitor_declares_where_its_headline_figure_comes_from(
    monitor: Monitor,
) -> None:
    """The card reads `/summary`, but its own page has to derive the same figure locally."""
    if not isinstance(monitor.viz, Gauge):
        pytest.skip(f"{monitor.id} is judged on its own series")
    assert monitor.kpi_sum_field, f"{monitor.id} draws a gauge but sums nothing"


def test_a_monitor_with_no_incident_age_does_not_order_by_age(monitor: Monitor) -> None:
    """`days_open` is optional now, and sorting every row by a missing figure is no order."""
    if monitor.sort_field != "days_open":
        return
    assert isinstance(monitor.viz, Sparkline), (
        f"{monitor.id} is judged on a benchmark, which suggests it reports no age to sort by"
    )


def test_the_alias_covers_every_variant(monitor: Monitor) -> None:
    assert TileViz.__args__ == (Sparkline, Gauge)
