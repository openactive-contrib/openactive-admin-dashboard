"""Open a table row's details in a pop-up, remembering the row by id rather than position.

A table selection is a position, and positions shift when rows re-sort or a filter
changes. The id is what we keep, so the pop-up always shows the row that was clicked.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import streamlit as st

#: Height in pixels of the pop-up's scrolling area: fits a laptop screen with the title.
DIALOG_BODY_HEIGHT = 620


def find_row[T](rows: Sequence[T], row_id: Callable[[T], str], wanted: str | None) -> T | None:
    """The row whose id is `wanted`, or None if there isn't one (or nothing is wanted)."""
    if wanted is None:
        return None
    return next((row for row in rows if row_id(row) == wanted), None)


def chosen_id[T](
    rows: Sequence[T],
    row_id: Callable[[T], str],
    position: int | None,
    last_position: int | None,
    current_id: str | None,
) -> str | None:
    """The id to open now.

    Only a *new* click changes it. The table keeps its selection across reloads, and after a
    save the rows may have re-sorted, so the same position can point at another row. Trusting
    the stored id instead keeps the pop-up on the row the steward actually opened.
    """
    if position is None:
        return None
    if position != last_position and position < len(rows):
        return row_id(rows[position])
    return current_id


def render_row_dialog[T](
    rows: Sequence[T],
    row_id: Callable[[T], str],
    position: int | None,
    *,
    key: str,
    table_key: str,
    title: Callable[[T], str],
    render: Callable[[T], None],
    actions: Callable[[T], None] | None = None,
) -> None:
    """Open the selected row in a large pop-up; closing it clears the table selection.

    `key` keeps each table's pop-up separate. `table_key` is the table's widget key, so
    closing the pop-up can clear its selection and it doesn't reopen on the next reload.
    `actions`, if given, is drawn under the scrolling area so it is always in view.
    """
    id_key, position_key = f"{key}_open_id", f"{key}_open_position"
    wanted = chosen_id(
        rows,
        row_id,
        position,
        st.session_state.get(position_key),
        st.session_state.get(id_key),
    )
    st.session_state[position_key] = position
    row = find_row(rows, row_id, wanted)
    if row is None:
        st.session_state.pop(id_key, None)
        return
    st.session_state[id_key] = wanted

    def close() -> None:
        for name in (id_key, position_key, table_key):
            st.session_state.pop(name, None)

    @st.dialog(title(row), width="large", on_dismiss=close)
    def show() -> None:
        # A fixed-height box so long details scroll inside the pop-up, not the whole page.
        with st.container(height=DIALOG_BODY_HEIGHT, border=False):
            render(row)
        if actions is not None:
            actions(row)

    show()
