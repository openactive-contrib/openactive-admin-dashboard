"""The knowledge base: searchable index, tag chips, doc view, and the access panel."""

from __future__ import annotations

import streamlit as st

from stewards.components import layout
from stewards.components.surface import card
from stewards.knowledge.loader import (
    Doc,
    all_docs,
    all_tags,
    get_doc,
    human_date,
    index_frame,
    newest_update,
    remember,
    search_docs,
)

CRUMB = "Knowledge base"
TITLE = "Internal documentation"

SELECTED_KEY = "docs_selected_slug"
RECENT_KEY = "docs_recent_slugs"
TAG_KEY = "docs_tag"

ALL_TAGS = "All"
SEARCH_PLACEHOLDER = 'Search titles, body text and tags — e.g. "orphan", "runbook", "stalls"'
ACCESS_BODY = (
    "Every page here sits behind Google SSO and is restricted to the `theodi.org` "
    "workspace. Nothing in this space is publishable."
)


def _select(slug: str) -> None:
    st.session_state[SELECTED_KEY] = slug
    st.session_state[RECENT_KEY] = remember(st.session_state.get(RECENT_KEY, ()), slug)


def count_label(total: int) -> str:
    """`1 document` / `6 documents`."""
    return f"{total} document" if total == 1 else f"{total} documents"


def _eyebrow(text: str, key: str) -> None:
    with st.container(key=key):
        st.markdown(text.upper())


def render_tags(tags: tuple[str, ...], key: str) -> None:
    if not tags:
        return
    with st.container(horizontal=True, key=key):
        for tag in tags:
            st.badge(tag, color="primary")


def render_result(doc: Doc) -> None:
    """One result card: title, excerpt, tags; date and owner on the right."""
    with card(f"doc_{doc.slug}"):
        body, meta = st.columns([5, 1], vertical_alignment="top")
        with body:
            with st.container(key=f"oadoctitle_{doc.slug}"):
                st.button(
                    doc.title,
                    key=f"open_doc_{doc.slug}",
                    type="tertiary",
                    on_click=_select,
                    args=(doc.slug,),
                )
            with st.container(key=f"oadocexcerpt_{doc.slug}"):
                st.markdown(doc.excerpt)
            render_tags(doc.tags, key=f"oadoctags_{doc.slug}")
        with meta, st.container(key=f"oadocmeta_{doc.slug}"):
            st.markdown(human_date(doc.updated))
            st.markdown(doc.owner)


def render_index(docs: tuple[Doc, ...]) -> None:
    with card("docs_search"):
        term = st.text_input(
            "Search",
            key="docs_search_term",
            placeholder=SEARCH_PLACEHOLDER,
            label_visibility="collapsed",
        )
        chosen = st.pills(
            "Tag",
            [ALL_TAGS, *all_tags(docs)],
            default=ALL_TAGS,
            key=TAG_KEY,
            label_visibility="collapsed",
        )

    tags = () if chosen in (None, ALL_TAGS) else (chosen,)
    results = search_docs(docs, term, tags)
    if not results:
        st.info("No document matches that search. Clear the search box or pick All.")
        return
    for doc in results:
        render_result(doc)


def render_access_panel(docs: tuple[Doc, ...]) -> None:
    with card("docs_access"):
        _eyebrow("Access", "oaeyebrow_access")
        with st.container(key="oapanelbody"):
            st.markdown(ACCESS_BODY)
        with st.container(key="oapanelrule"):
            st.divider()
        _eyebrow("Recently viewed", "oaeyebrow_recent")
        recent = st.session_state.get(RECENT_KEY, ())
        if not recent:
            st.caption("Documents you open appear here.")
            return
        for slug in recent:
            try:
                doc = get_doc(slug, docs)
            except KeyError:
                continue
            st.button(
                doc.title,
                key=f"recent_{slug}",
                type="tertiary",
                on_click=_select,
                args=(slug,),
            )


def render_doc(doc: Doc) -> None:
    body, side = st.columns([3, 1], vertical_alignment="top")
    with body:
        st.button(
            "← All documents", key="docs_back", type="tertiary", on_click=_select, args=("",)
        )
        render_tags(doc.tags, key=f"oadocviewtags_{doc.slug}")
        if doc.is_restricted:
            st.markdown(":red-badge[internal only]")
        st.title(doc.title, anchor=False)
        st.caption(f"Owner: {doc.owner} · updated {doc.updated_label}")
        st.markdown(doc.body)
    with side:
        _eyebrow("On this page", "oaeyebrow_onthispage")
        for heading in doc.headings:
            st.markdown(f"- {heading}")


def render_docs_page() -> None:
    docs = all_docs()
    slug = st.session_state.get(SELECTED_KEY, "")

    if slug:
        try:
            doc = get_doc(slug, docs)
        except KeyError:
            st.session_state[SELECTED_KEY] = ""
            st.warning("That document no longer exists. Showing the index instead.")
        else:
            layout.render_header(CRUMB, doc.title)
            render_doc(doc)
            layout.render_footer("", note="Documentation is internal to the ODI workspace.")
            return

    latest = newest_update(docs)
    layout.render_header(
        CRUMB,
        TITLE,
        export=index_frame(docs),
        export_name="documentation",
        export_stamp=latest.isoformat() if latest else "",
        meta_lines=(
            f"Updated `{human_date(latest)}`",
            f"{count_label(len(docs))} · internal only",
        ),
    )
    index, panel = st.columns([3.3, 1], vertical_alignment="top")
    with index:
        render_index(docs)
    with panel:
        render_access_panel(docs)
    layout.render_footer("", note="Documentation is internal to the ODI workspace.")
