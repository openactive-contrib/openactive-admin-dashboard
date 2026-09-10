"""One test module that validates the whole registry, so future monitors are covered free."""

from __future__ import annotations

from pathlib import Path

import pytest

from fixture_loader import load_sample
from stewards.api.models import DetailModel, IncidentPage
from stewards.monitors.registry import (
    MONITOR_REGISTRY,
    Group,
    Monitor,
    Severity,
)
from stewards.monitors.transforms import expand, resolve_field

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "stewards"
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "fixtures"

pytestmark = pytest.mark.parametrize(
    "monitor", MONITOR_REGISTRY, ids=[m.id for m in MONITOR_REGISTRY]
)


def test_id_is_unique(monitor: Monitor) -> None:
    assert [m.id for m in MONITOR_REGISTRY].count(monitor.id) == 1


def test_group_and_severity_are_known_values(monitor: Monitor) -> None:
    assert monitor.group in set(Group)
    assert monitor.severity in set(Severity)


def test_page_module_exists(monitor: Monitor) -> None:
    assert monitor.page, f"{monitor.id} declares no page"
    assert (APP_ROOT / monitor.page).is_file()


def test_blurb_and_unit_are_present(monitor: Monitor) -> None:
    assert len(monitor.blurb) > 80
    assert "!" not in monitor.blurb
    assert monitor.unit


def test_threshold_is_positive(monitor: Monitor) -> None:
    assert monitor.threshold_days >= 1


def test_columns_have_unique_labels(monitor: Monitor) -> None:
    labels = [c.label for c in monitor.columns]
    assert len(labels) == len(set(labels)), f"{monitor.id} reuses a column label"


def test_first_column_is_the_primary_one(monitor: Monitor) -> None:
    assert monitor.columns[0].primary


def test_sample_payload_exists(monitor: Monitor) -> None:
    assert (SAMPLE_DIR / f"{monitor.id}_incidents.json").is_file()
    assert (SAMPLE_DIR / f"{monitor.id}_trend.json").is_file()


def test_every_column_field_resolves_against_the_payload(monitor: Monitor) -> None:
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    assert page.data, f"{monitor.id} sample payload has no incidents"
    rows = expand(monitor, page.data)
    assert rows, f"{monitor.id} sample payload expands to no rows"
    for row in rows:
        for col in monitor.columns:
            resolve_field(monitor, row, col.field)  # must not raise


def test_every_declared_column_reports_a_value_somewhere(monitor: Monitor) -> None:
    """A column no payload row can fill is a typo in the field path, not a sparse column."""
    rows = expand(
        monitor, IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents")).data
    )
    for col in monitor.columns:
        assert any(resolve_field(monitor, row, col.field) is not None for row in rows), (
            f"{monitor.id} column {col.field!r} resolves to None on every payload row"
        )


def test_declared_filters_resolve_and_are_labelled(monitor: Monitor) -> None:
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    rows = expand(monitor, page.data)
    for spec in monitor.filters:
        assert spec.label
        assert any(resolve_field(monitor, row, spec.field) is not None for row in rows)


def test_detail_model_validates_every_payload_detail(monitor: Monitor) -> None:
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    for incident in page.data:
        assert isinstance(monitor.detail_model.model_validate(incident.detail), DetailModel)


def test_payload_monitor_id_matches_the_registry(monitor: Monitor) -> None:
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    assert {i.monitor_id for i in page.data} == {monitor.id}


def test_meta_chips_state_the_threshold(monitor: Monitor) -> None:
    assert f"contact after {monitor.threshold_days}d" in monitor.meta_chips
