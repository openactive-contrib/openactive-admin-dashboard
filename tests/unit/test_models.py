"""The two model-level rules that let a monitor's own payload through unchanged.

`Incident` folds top-level keys it does not declare into `detail`, and `DetailModel` lets an
explicit null fall back to a field's default. Both exist so that adding a monitor is a
registry entry rather than an edit to the shared incident model.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from stewards.api.models import (
    DetailModel,
    Incident,
    OrphanedChildrenDetail,
    OrphanKind,
    StallDetail,
)


def incident(**overrides: object) -> Incident:
    base: dict[str, object] = {
        "monitor_id": "m",
        "publisher_id": "pub_x",
        "publisher_name": "Publisher X",
        "past_threshold": False,
        "status": "open",
    }
    return Incident.model_validate(base | overrides)


# --- folding a monitor's own measurements into detail --------------------------------------


def test_an_undeclared_top_level_key_is_kept_in_detail() -> None:
    assert incident(orphan_count=7).detail == {"orphan_count": 7}


def test_a_declared_field_is_not_also_folded_in() -> None:
    row = incident(status="open", feed_name="events")
    assert row.detail == {}
    assert row.feed_name == "events"


def test_a_payload_with_no_extras_keeps_the_detail_it_was_given() -> None:
    assert incident(detail={"last_modified": "2026-08-01"}).detail == {
        "last_modified": "2026-08-01"
    }
    assert incident().detail == {}


def test_the_nested_value_wins_where_both_carry_the_same_name() -> None:
    """The nested one is the explicit one; folding must never overwrite it."""
    assert incident(orphan_count=1, detail={"orphan_count": 9}).detail["orphan_count"] == 9


@pytest.mark.parametrize("detail", [["not", "an", "object"], "a string", 7])
def test_a_detail_that_is_not_an_object_is_left_for_the_field_to_reject(detail: object) -> None:
    """The fold must not disguise a malformed payload as a plausible one.

    Dropping the bad value and keeping the extras would hand the page a half-real row where
    it should be told the API's shape is wrong.
    """
    with pytest.raises(ValidationError):
        incident(orphan_count=1, detail=detail)
    with pytest.raises(ValidationError):
        incident(detail=detail)


def test_an_explicit_null_detail_reads_as_an_empty_one() -> None:
    """The batch sends null for what it did not compute, here as everywhere else."""
    assert incident(detail=None).detail == {}
    assert incident(detail=None, orphan_count=3).detail == {"orphan_count": 3}


def test_a_payload_that_is_not_an_object_at_all_is_rejected_not_folded() -> None:
    with pytest.raises(ValidationError):
        Incident.model_validate(["not", "an", "object"])


def test_folding_survives_a_round_trip_through_the_model() -> None:
    """Re-validating a dumped incident must not fold `detail` into itself."""
    once = incident(orphan_count=7)
    twice = Incident.model_validate(once.model_dump())
    assert twice.detail == {"orphan_count": 7}


# --- an incident that reports no age -------------------------------------------------------


def test_the_age_fields_are_optional() -> None:
    row = incident()
    assert row.days_open is None
    assert row.first_detected is None


def test_the_age_fields_still_parse_where_the_api_sends_them() -> None:
    row = incident(first_detected="2026-08-14", days_open=7)
    assert row.first_detected == date(2026, 8, 14)
    assert row.days_open == 7


@pytest.mark.parametrize("field", ["monitor_id", "publisher_id", "publisher_name", "status"])
def test_the_fields_that_identify_an_incident_are_still_required(field: str) -> None:
    """Optional age must not have loosened the model into accepting anything."""
    payload = {
        "monitor_id": "m",
        "publisher_id": "pub_x",
        "publisher_name": "Publisher X",
        "past_threshold": False,
        "status": "open",
    }
    del payload[field]
    with pytest.raises(ValidationError):
        Incident.model_validate(payload)


# --- an explicit null in a detail payload --------------------------------------------------


def test_a_null_collection_falls_back_to_the_empty_default() -> None:
    """The regression: a null `by_kind` raised where an absent key simply defaulted.

    The batch sends null for a figure it did not compute, and a detail model declares a
    collection as an empty tuple rather than as optional.
    """
    assert OrphanedChildrenDetail.model_validate({"by_kind": None}).by_kind == ()
    assert (
        OrphanedChildrenDetail.model_validate({"missing_parents": None}).missing_parents == ()
    )


def test_a_null_optional_field_still_reads_as_none() -> None:
    assert StallDetail.model_validate({"last_modified": None}).last_modified is None
    assert OrphanKind.model_validate({"orphan_count": None}).orphan_count is None


def test_an_absent_key_and_an_explicit_null_agree() -> None:
    assert OrphanedChildrenDetail.model_validate({"orphan_count": None}) == (
        OrphanedChildrenDetail.model_validate({})
    )


def test_a_detail_payload_that_is_not_an_object_is_still_rejected() -> None:
    with pytest.raises(ValidationError):
        DetailModel.model_validate("not an object")


def test_a_real_value_is_untouched() -> None:
    detail = OrphanedChildrenDetail.model_validate(
        {"orphan_count": 5, "by_kind": [{"kind": "Slot", "orphan_count": 5}]}
    )
    assert detail.orphan_count == 5
    assert detail.by_kind[0].kind == "Slot"


# --- the two shares --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("orphans", "checked", "expected"),
    [(0, 100, 0.0), (50, 100, 50.0), (100, 100, 100.0), (5, 0, None), (None, 10, None)],
)
def test_a_breakdown_reports_its_share_of_what_was_checked(
    orphans: int | None, checked: int, expected: float | None
) -> None:
    """Zero checked is not zero orphaned: there is no share, rather than a share of 0%."""
    kind = OrphanKind(orphan_count=orphans, checked_count=checked)
    if expected is None:
        assert kind.orphan_percent is None
    else:
        assert kind.orphan_percent == pytest.approx(expected)


@pytest.mark.parametrize(
    ("share", "expected"), [(None, None), (0.0, 0.0), (0.397, 39.7), (1.0, 100.0)]
)
def test_a_dataset_reports_the_api_fraction_as_a_percentage(
    share: float | None, expected: float | None
) -> None:
    """Named to match `OrphanKind`, so a dataset with no breakdown falls back onto it."""
    detail = OrphanedChildrenDetail(orphan_share=share)
    if expected is None:
        assert detail.orphan_percent is None
    else:
        assert detail.orphan_percent == pytest.approx(expected)
