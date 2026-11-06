"""Finding the selected row by id."""

from __future__ import annotations

from stewards.components.row_dialog import find_row

ROWS = [{"id": "a"}, {"id": "b"}, {"id": "c"}]


def by_id(row: dict[str, str]) -> str:
    return row["id"]


def test_finds_the_row_with_that_id() -> None:
    assert find_row(ROWS, by_id, "b") == {"id": "b"}


def test_finds_it_wherever_it_moved_to() -> None:
    assert find_row(list(reversed(ROWS)), by_id, "b") == {"id": "b"}


def test_nothing_wanted_finds_nothing() -> None:
    assert find_row(ROWS, by_id, None) is None


def test_a_row_that_is_gone_finds_nothing() -> None:
    # e.g. filtered out, or deleted by someone else
    assert find_row(ROWS, by_id, "z") is None
