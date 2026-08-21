"""The knowledge base: searchable index, tag filter, and a full markdown view."""

from __future__ import annotations

import streamlit as st

from stewards.components import layout
from stewards.knowledge.loader import Doc, all_docs, all_tags, get_doc, search_docs

SELECTED_KEY = "docs_selected_slug"
TITLE = "Internal documentation"
CRUMB = "Knowledge base"


def _select(slug: str) -> None:
    st.session_state[SELECTED_KEY] = slug


def render_index(docs: tuple[Doc, ...]) -> None:
    with st.container(border=True):
        term = st.text_input(
            "Search",
            key="docs_search",
            placeholder="Search titles, body text and tags — e.g. “stall”, “runbook”",
            label_visibility="collapsed",
        )
        tags = st.multiselect(
            "Tags",
            all_tags(docs),
            key="docs_tags",
            label_visibility="collapsed",
            placeholder="Filter by tag",
        )

    results = search_docs(docs, term, tags)
    st.caption(f"{len(results)} of {len(docs)} documents")
    if not results:
        st.info("No document matches that search. Clear the search box or the tag filter.")
        return

    for doc in results:
        with st.container(border=True):
            body, side = st.columns([4, 1], vertical_alignment="top")
            with body:
                st.markdown(f"**{doc.title}**")
                st.caption(doc.excerpt)
                st.markdown(" ".join(f"`{tag}`" for tag in doc.tags))
            with side:
                st.caption(f"updated {doc.updated_label}")
                st.caption(doc.owner)
                st.button(
                    "Open",
                    key=f"open_doc_{doc.slug}",
                    on_click=_select,
                    args=(doc.slug,),
                    use_container_width=True,
                )


def render_doc(doc: Doc) -> None:
    body, side = st.columns([3, 1], vertical_alignment="top")
    with body:
        st.button("← All documents", key="docs_back", on_click=_select, args=("",))
        tags = " ".join(f"`{tag}`" for tag in doc.tags)
        if doc.is_restricted:
            st.markdown(f"{tags} :red[**internal only**]")
        else:
            st.markdown(tags)
        st.title(doc.title, anchor=False)
        st.caption(f"Owner: {doc.owner} · updated {doc.updated_label}")
        st.markdown(doc.body)
    with side:
        st.caption("ON THIS PAGE")
        for heading in doc.headings:
            st.markdown(f"- {heading}")


def render_docs_page() -> None:
    docs = all_docs()
    st.caption(CRUMB.upper())
    slug = st.session_state.get(SELECTED_KEY, "")

    if slug:
        try:
            doc = get_doc(slug, docs)
        except KeyError:
            st.session_state[SELECTED_KEY] = ""
            st.warning("That document no longer exists. Showing the index instead.")
        else:
            render_doc(doc)
            layout.render_footer("", note="Documentation is internal to the ODI workspace.")
            return

    st.title(TITLE, anchor=False)
    st.caption(
        "Every page here sits behind Google SSO and is restricted to the theodi.org "
        "workspace. Nothing in this space is publishable."
    )
    st.divider()
    render_index(docs)
    layout.render_footer("", note="Documentation is internal to the ODI workspace.")
