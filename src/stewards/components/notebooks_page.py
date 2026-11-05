"""The Notebooks page: the notebooks from `notebooks/notebooks.yaml`, one row each.

Not a data page — nothing here reads the API, so the header names the manifest rather than a
snapshot. Each row links out to Colab, which opens the notebook from GitHub in a new tab.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from stewards import notebooks
from stewards.components import layout
from stewards.components.surface import card
from stewards.notebooks import ManifestError, Notebook, NotebookManifest

CRUMB = "Reference"
TITLE = "Notebooks"
SOURCE = f"`{notebooks.NOTEBOOKS_DIR_NAME}/{notebooks.MANIFEST_PATH.name}` · Google Colab"

COLAB_ICON = ":material/play_circle:"
COLAB_HELP = "Open in Colab"
GITHUB_ICON = ":material/code:"
GITHUB_HELP = "View on GitHub"


def render_notebooks_page(path: Path = notebooks.MANIFEST_PATH) -> None:
    try:
        manifest = notebooks.load_manifest(path)
    except ManifestError as exc:
        layout.render_error_header(CRUMB, TITLE)
        st.error(f"**The notebook manifest could not be read.**\n\n{exc}")
        return

    layout.render_reference_header(CRUMB, TITLE, SOURCE)
    with card("blurb_notebooks"):
        st.markdown(
            "Notebooks for ad-hoc data analysis. See the "
            f"[README]({manifest.readme_url()}) for more details."
        )
    if not manifest.notebooks:
        st.info("No notebooks are listed in the manifest yet.")
        return
    with card("notebooks"):
        for notebook in manifest.notebooks:
            _render_row(manifest, notebook)


def _render_row(manifest: NotebookManifest, notebook: Notebook) -> None:
    """Title and description left, the two links as icon buttons at the end of the row."""
    with st.container(horizontal=True, vertical_alignment="center"):
        with st.container(width="stretch", gap=None):
            st.markdown(f"**{notebook.title}**")
            meta = " · ".join(filter(None, [notebook.description, ", ".join(notebook.tags)]))
            if meta:
                st.caption(meta)
        for url, icon, tip in (
            (manifest.colab_url(notebook), COLAB_ICON, COLAB_HELP),
            (manifest.github_url(notebook), GITHUB_ICON, GITHUB_HELP),
        ):
            st.link_button("", url, icon=icon, help=tip, type="tertiary")
