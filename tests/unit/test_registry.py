"""One test module that validates the whole registry, so future monitors are covered free."""

from __future__ import annotations

from pathlib import Path

import pytest

from fixture_loader import load_sample
from stewards.api.models import DetailModel, FeedQualityResponse, IncidentPage
from stewards.monitors import quality
from stewards.monitors.registry import (
    MONITOR_REGISTRY,
    Group,
    Monitor,
    Severity,
    Source,
)
from stewards.monitors.transforms import detail_caption, detail_items, expand, resolve_field

APP_ROOT = Path(__file__).resolve().parents[2] / "src" / "stewards"
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "fixtures"

pytestmark = pytest.mark.parametrize(
    "monitor", MONITOR_REGISTRY, ids=[m.id for m in MONITOR_REGISTRY]
)


def backed_by(monitor: Monitor, source: Source) -> None:
    """Skip a check that only means something for one kind of backing read.

    The registry holds both kinds, and a quality monitor has no incidents to validate — no
    `monitor_id` on its rows, no detail blob, no contact threshold and no publisher email.
    Gating on the declared source rather than on the monitor id keeps this module
    parametrised over the whole registry, so a future monitor of either kind is covered free.
    """
    if monitor.source is not source:
        pytest.skip(f"{monitor.id} is backed by the {monitor.source.value} read")


def quality_rows(monitor: Monitor) -> tuple[quality.QualityRow, ...]:
    payload = FeedQualityResponse.model_validate(load_sample(f"{monitor.id}_quality"))
    return quality.build_rows(payload.data)


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
    backed_by(monitor, Source.INCIDENTS)
    assert (SAMPLE_DIR / f"{monitor.id}_incidents.json").is_file()
    assert (SAMPLE_DIR / f"{monitor.id}_trend.json").is_file()


def test_every_column_field_resolves_against_the_payload(monitor: Monitor) -> None:
    backed_by(monitor, Source.INCIDENTS)
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    assert page.data, f"{monitor.id} sample payload has no incidents"
    rows = expand(monitor, page.data)
    assert rows, f"{monitor.id} sample payload expands to no rows"
    for row in rows:
        for col in monitor.columns:
            resolve_field(monitor, row, col.field)  # must not raise


def test_every_declared_column_reports_a_value_somewhere(monitor: Monitor) -> None:
    """A column no payload row can fill is a typo in the field path, not a sparse column."""
    backed_by(monitor, Source.INCIDENTS)
    rows = expand(
        monitor, IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents")).data
    )
    for col in monitor.columns:
        assert any(resolve_field(monitor, row, col.field) is not None for row in rows), (
            f"{monitor.id} column {col.field!r} resolves to None on every payload row"
        )


def test_declared_filters_resolve_and_are_labelled(monitor: Monitor) -> None:
    backed_by(monitor, Source.INCIDENTS)
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    rows = expand(monitor, page.data)
    for spec in monitor.filters:
        assert spec.label
        assert any(resolve_field(monitor, row, spec.field) is not None for row in rows)


def test_detail_model_validates_every_payload_detail(monitor: Monitor) -> None:
    backed_by(monitor, Source.INCIDENTS)
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    for incident in page.data:
        assert isinstance(monitor.detail_model.model_validate(incident.detail), DetailModel)


def test_payload_monitor_id_matches_the_registry(monitor: Monitor) -> None:
    backed_by(monitor, Source.INCIDENTS)
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    assert {i.monitor_id for i in page.data} == {monitor.id}


def test_meta_chips_state_the_threshold(monitor: Monitor) -> None:
    backed_by(monitor, Source.INCIDENTS)
    assert f"contact after {monitor.threshold_days}d" in monitor.meta_chips


def test_a_declared_row_detail_resolves_against_the_payload(monitor: Monitor) -> None:
    """Its list field, its columns and its caption's count field must all be real paths."""
    backed_by(monitor, Source.INCIDENTS)
    spec = monitor.row_detail
    if spec is None:
        return
    assert spec.title
    assert spec.caption
    assert spec.columns[0].primary
    labels = [col.label for col in spec.columns]
    assert len(labels) == len(set(labels)), f"{monitor.id} row detail reuses a column label"

    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    seen = [detail_items(monitor, incident, spec) for incident in page.data]
    assert any(seen), f"{monitor.id} declares a row detail no payload row fills"
    for incident, rows in zip(page.data, seen, strict=True):
        # The caption must format cleanly whether or not the count field is reported.
        assert "{count}" not in detail_caption(monitor, incident, spec)
        for row in rows:
            for col in spec.columns:
                resolve_field(monitor, row, col.field)  # must not raise


def test_a_row_detail_column_reports_a_value_somewhere(monitor: Monitor) -> None:
    backed_by(monitor, Source.INCIDENTS)
    spec = monitor.row_detail
    if spec is None:
        return
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    rows = [row for incident in page.data for row in detail_items(monitor, incident, spec)]
    for col in spec.columns:
        assert any(resolve_field(monitor, row, col.field) is not None for row in rows), (
            f"{monitor.id} row-detail column {col.field!r} resolves to None on every item"
        )


def test_the_identifying_email_fields_resolve_against_the_payload(monitor: Monitor) -> None:
    """A label with an unresolvable path would put a permanent em dash in publisher copy."""
    backed_by(monitor, Source.INCIDENTS)
    assert monitor.email_fields, f"{monitor.id} names nothing in its email"
    assert monitor.entity
    labels = [label for label, _ in monitor.email_fields]
    assert len(labels) == len(set(labels)), f"{monitor.id} reuses an email label"

    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    for label, path in monitor.email_fields:
        assert label
        assert any(resolve_field(monitor, i, path) is not None for i in page.data), (
            f"{monitor.id} email field {path!r} resolves to None on every payload row"
        )


def test_the_queue_summary_field_resolves_against_the_payload(monitor: Monitor) -> None:
    backed_by(monitor, Source.INCIDENTS)
    """It identifies the row in the contact queue and seeds the page's search."""
    page = IncidentPage.model_validate(load_sample(f"{monitor.id}_incidents"))
    rows = expand(monitor, page.data)
    assert any(resolve_field(monitor, row, monitor.summary_field) is not None for row in rows)


# --- quality monitors ---------------------------------------------------------------------
#
# The same bargain the incident checks make, against the other backing read: parametrised
# over the whole registry, so the next quality monitor is validated the moment it lands.


def test_a_quality_monitor_ships_a_sample_snapshot(monitor: Monitor) -> None:
    backed_by(monitor, Source.QUALITY)
    assert (SAMPLE_DIR / f"{monitor.id}_quality.json").is_file()


def test_every_quality_column_field_resolves_against_the_payload(monitor: Monitor) -> None:
    backed_by(monitor, Source.QUALITY)
    rows = quality_rows(monitor)
    assert rows, f"{monitor.id} sample payload has no feeds"
    for row in rows:
        for col in monitor.columns:
            quality.resolve(row, col.field)  # must not raise


def test_every_declared_quality_column_reports_a_value_somewhere(monitor: Monitor) -> None:
    """A column no payload row can fill is a typo in the field name, not a sparse column."""
    backed_by(monitor, Source.QUALITY)
    rows = quality_rows(monitor)
    for col in monitor.columns:
        assert any(quality.resolve(row, col.field) is not None for row in rows), (
            f"{monitor.id} column {col.field!r} resolves to None on every payload row"
        )


def test_declared_quality_filters_resolve_and_are_labelled(monitor: Monitor) -> None:
    backed_by(monitor, Source.QUALITY)
    rows = quality_rows(monitor)
    for spec in monitor.filters:
        assert spec.label
        assert quality.filter_options(rows, spec.field), (
            f"{monitor.id} filter {spec.field!r} has no options in the payload"
        )


def test_a_quality_monitor_sorts_on_a_field_its_rows_report(monitor: Monitor) -> None:
    backed_by(monitor, Source.QUALITY)
    rows = quality_rows(monitor)
    assert any(quality.resolve(row, monitor.sort_field) is not None for row in rows)


def test_a_quality_monitor_states_no_contact_threshold(monitor: Monitor) -> None:
    """Nothing in a quality snapshot ages, so a threshold chip or toggle would be a claim
    the data cannot support."""
    backed_by(monitor, Source.QUALITY)
    assert not monitor.has_threshold_filter
    assert not any(chip.startswith("contact after") for chip in monitor.meta_chips)


def test_a_quality_payload_carries_a_summary_block(monitor: Monitor) -> None:
    """The page's five figures and the whole of its card come off this block."""
    backed_by(monitor, Source.QUALITY)
    payload = FeedQualityResponse.model_validate(load_sample(f"{monitor.id}_quality"))
    assert payload.summary.total_feeds
    assert payload.summary.score_buckets
    assert payload.summary.completeness
