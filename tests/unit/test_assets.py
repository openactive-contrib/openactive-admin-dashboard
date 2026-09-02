"""The bundled brand artwork.

The OpenActive lockup is a raster asset, outside the reach of the palette guard in
`test_theme.py`. What these tests pin is what the app actually depends on: that it ships,
and that it is the shape `st.logo` needs. The plaque that keeps its navy wordmark legible
on the dark sidebar is asserted in `test_surface.py`.
"""

from __future__ import annotations

import struct

from stewards.components import nav

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_the_logo_ships_with_the_package() -> None:
    assert nav.LOGO.is_file(), f"{nav.LOGO} is missing — st.logo fails at render time"


def test_the_logo_is_a_horizontal_png_with_transparency() -> None:
    """`st.logo` puts the lockup on two grounds, so it must not carry one of its own."""
    data = nav.LOGO.read_bytes()
    assert data[:8] == PNG_MAGIC
    width, height, _, colour_type = struct.unpack(">IIBB", data[16:26])
    assert colour_type in {4, 6}, "the lockup needs alpha, not a baked-in background"
    assert width > height, "st.logo scales by height; this is a horizontal lockup"
