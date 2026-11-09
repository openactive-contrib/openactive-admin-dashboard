"""Finding the selected row by id."""

from __future__ import annotations

from stewards.components.row_dialog import chosen_id, find_row

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


def test_a_new_click_opens_that_row() -> None:
    assert chosen_id(ROWS, by_id, 2, None, None) == "c"
    assert chosen_id(ROWS, by_id, 2, 0, "a") == "c"


def test_the_same_selection_keeps_the_row_already_open() -> None:
    # After a save the rows re-sorted, so position 0 is now another row; stay on "b".
    assert chosen_id(ROWS, by_id, 0, 0, "b") == "b"


def test_no_selection_opens_nothing() -> None:
    assert chosen_id(ROWS, by_id, None, 1, "b") is None


def test_a_position_past_the_end_keeps_the_row_already_open() -> None:
    assert chosen_id(ROWS, by_id, 9, 1, "b") == "b"
