"""Open a table row's details in a pop-up, remembering the row by id rather than position.

A table selection is a position, and positions shift when rows re-sort or a filter
changes. The id is what we keep, so the pop-up always shows the row that was clicked.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence


def find_row[T](rows: Sequence[T], row_id: Callable[[T], str], wanted: str | None) -> T | None:
    """The row whose id is `wanted`, or None if there isn't one (or nothing is wanted)."""
    if wanted is None:
        return None
    return next((row for row in rows if row_id(row) == wanted), None)
