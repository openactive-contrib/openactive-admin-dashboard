"""Knowledge base parsing, search and heading extraction."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from stewards.knowledge.loader import (
    DOCS_DIR,
    Doc,
    DocError,
    all_docs,
    all_tags,
    excerpt_of,
    get_doc,
    load_docs,
    parse_doc,
    search_docs,
)

SAMPLE = """\
---
title: Runbook — example
tags: [runbook, stalls]
owner: Data Infrastructure
updated: 2026-08-14
sensitivity: restricted
---

A feed is stalled when its modified timestamp stops advancing.

## Detection

The nightly job compares max(modified).

## Triage

Check the dataset monitor first.
"""


def test_front_matter_is_parsed() -> None:
    doc = parse_doc("example", SAMPLE)
    assert doc.title == "Runbook — example"
    assert doc.tags == ("runbook", "stalls")
    assert doc.owner == "Data Infrastructure"
    assert doc.updated == date(2026, 8, 14)
    assert doc.is_restricted


def test_body_excludes_the_front_matter() -> None:
    assert parse_doc("example", SAMPLE).body.startswith("A feed is stalled")


def test_headings_are_the_h2s_in_order() -> None:
    assert parse_doc("example", SAMPLE).headings == ("Detection", "Triage")


def test_a_document_without_headings_has_an_empty_list() -> None:
    doc = parse_doc("x", "---\ntitle: X\n---\n\nJust prose.\n")
    assert doc.headings == ()


def test_missing_front_matter_is_an_error() -> None:
    with pytest.raises(DocError, match="missing front matter"):
        parse_doc("x", "# Just a heading\n")


def test_missing_title_is_an_error() -> None:
    with pytest.raises(DocError, match="no title"):
        parse_doc("x", "---\ntags: [a]\n---\n\nBody.\n")


def test_defaults_when_optional_front_matter_is_absent() -> None:
    doc = parse_doc("x", "---\ntitle: X\n---\n\nBody.\n")
    assert doc.tags == ()
    assert doc.owner == "unassigned"
    assert doc.updated is None
    assert doc.updated_label == "undated"
    assert doc.sensitivity == "internal"
    assert not doc.is_restricted


def test_an_unparseable_date_is_treated_as_undated() -> None:
    doc = parse_doc("x", "---\ntitle: X\nupdated: 14 Aug 2026\n---\n\nBody.\n")
    assert doc.updated is None


def test_excerpt_skips_headings_and_code_fences() -> None:
    body = "## Heading\n\n```sql\nSELECT 1\n```\n\nThe real first paragraph.\n"
    assert excerpt_of(body) == "The real first paragraph."


def test_excerpt_is_truncated_with_an_ellipsis() -> None:
    excerpt = excerpt_of("word " * 200, limit=40)
    assert len(excerpt) == 40
    assert excerpt.endswith("…")


def test_excerpt_of_an_empty_body_is_empty() -> None:
    assert excerpt_of("") == ""


def test_load_docs_reads_the_directory(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntitle: A\nupdated: 2026-01-01\n---\n\nBody A.\n")
    (tmp_path / "b.md").write_text("---\ntitle: B\nupdated: 2026-06-01\n---\n\nBody B.\n")
    docs = load_docs(tmp_path)
    assert [d.title for d in docs] == ["B", "A"]  # newest first


def test_load_docs_of_an_empty_directory_is_empty(tmp_path: Path) -> None:
    assert load_docs(tmp_path) == ()


def test_shipped_docs_all_parse() -> None:
    docs = all_docs()
    assert docs
    assert all(isinstance(doc, Doc) and doc.title for doc in docs)
    assert DOCS_DIR.is_dir()


def test_the_stalls_runbook_is_shipped_and_tagged_as_a_runbook() -> None:
    doc = get_doc("single-feed-stalls-runbook")
    assert "runbook" in doc.tags
    assert doc.headings
    assert doc.is_restricted


def test_get_doc_rejects_an_unknown_slug() -> None:
    with pytest.raises(KeyError, match="unknown document"):
        get_doc("no-such-doc")


def test_all_tags_is_sorted_and_deduplicated() -> None:
    docs = (
        parse_doc("a", "---\ntitle: A\ntags: [b, a]\n---\n\nBody.\n"),
        parse_doc("b", "---\ntitle: B\ntags: [a, c]\n---\n\nBody.\n"),
    )
    assert all_tags(docs) == ("a", "b", "c")


def test_all_tags_of_nothing_is_empty() -> None:
    assert all_tags(()) == ()


def test_search_matches_title_body_and_tags() -> None:
    docs = (parse_doc("example", SAMPLE),)
    assert search_docs(docs, "runbook")
    assert search_docs(docs, "NIGHTLY JOB")
    assert search_docs(docs, "stalls")
    assert search_docs(docs, "orphan") == []


def test_search_with_no_term_returns_everything() -> None:
    docs = (parse_doc("example", SAMPLE),)
    assert search_docs(docs) == list(docs)


def test_tag_filter_is_an_and() -> None:
    docs = (parse_doc("example", SAMPLE),)
    assert search_docs(docs, tags=["runbook", "stalls"])
    assert search_docs(docs, tags=["runbook", "coverage"]) == []


def test_tag_filter_is_case_insensitive() -> None:
    docs = (parse_doc("example", SAMPLE),)
    assert search_docs(docs, tags=["RUNBOOK"])


def test_search_of_no_documents_is_empty() -> None:
    assert search_docs((), "anything") == []


def test_front_matter_lines_without_a_colon_are_skipped() -> None:
    doc = parse_doc("x", "---\ntitle: X\nthis line has no colon\n\ntags: [a]\n---\n\nBody.\n")
    assert doc.title == "X"
    assert doc.tags == ("a",)


def test_quoted_front_matter_values_are_unwrapped() -> None:
    doc = parse_doc("x", "---\ntitle: \"X\"\nowner: 'Data Infrastructure'\n---\n\nBody.\n")
    assert doc.title == "X"
    assert doc.owner == "Data Infrastructure"


def test_all_docs_is_cached_and_returns_the_same_tuple() -> None:
    assert all_docs() is all_docs()
