"""Loads the markdown knowledge base. No Streamlit, no third-party front-matter parser.

Front matter is a small fixed set of keys (`title`, `tags`, `owner`, `updated`,
`sensitivity`), so a hand-rolled reader keeps the dependency list short and the failure mode
obvious.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

RESTRICTED = "restricted"
EXCERPT_CHARS = 220

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_H2 = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class DocError(ValueError):
    """A markdown file in docs/ is missing or malformed front matter."""


@dataclass(frozen=True, slots=True)
class Doc:
    slug: str
    title: str
    tags: tuple[str, ...]
    owner: str
    updated: date | None
    sensitivity: str
    body: str

    @property
    def is_restricted(self) -> bool:
        return self.sensitivity == RESTRICTED

    @property
    def excerpt(self) -> str:
        return excerpt_of(self.body)

    @property
    def headings(self) -> tuple[str, ...]:
        return tuple(_H2.findall(self.body))

    @property
    def updated_label(self) -> str:
        return self.updated.isoformat() if self.updated else "undated"


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise DocError("missing front matter")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    return fields, text[match.end() :].lstrip("\n")


def _parse_tags(raw: str) -> tuple[str, ...]:
    cleaned = raw.strip().strip("[]")
    return tuple(t.strip().strip("\"'") for t in cleaned.split(",") if t.strip())


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip().strip("\"'"))
    except ValueError:
        return None


def parse_doc(slug: str, text: str) -> Doc:
    """Parse one markdown document. Raises `DocError` when front matter is absent."""
    fields, body = _split_front_matter(text)
    if "title" not in fields:
        raise DocError(f"{slug}: front matter has no title")
    return Doc(
        slug=slug,
        title=fields["title"].strip("\"'"),
        tags=_parse_tags(fields.get("tags", "")),
        owner=fields.get("owner", "unassigned").strip("\"'"),
        updated=_parse_date(fields.get("updated", "")),
        sensitivity=fields.get("sensitivity", "internal").strip("\"'").lower(),
        body=body,
    )


def excerpt_of(body: str, limit: int = EXCERPT_CHARS) -> str:
    """First prose paragraph, trimmed — headings and code fences skipped."""
    for block in body.split("\n\n"):
        text = " ".join(block.split())
        if not text or text.startswith(("#", "```", "|", "-", ">")):
            continue
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    return ""


def load_docs(directory: Path | None = None) -> tuple[Doc, ...]:
    """Every doc in the directory, newest first, then alphabetical."""
    root = directory or DOCS_DIR
    docs = [
        parse_doc(path.stem, path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*.md"))
    ]
    return tuple(sorted(docs, key=lambda d: (d.updated or date.min, d.title), reverse=True))


@lru_cache(maxsize=1)
def all_docs() -> tuple[Doc, ...]:
    return load_docs()


def get_doc(slug: str, docs: Sequence[Doc] | None = None) -> Doc:
    pool = docs if docs is not None else all_docs()
    for doc in pool:
        if doc.slug == slug:
            return doc
    raise KeyError(f"unknown document {slug!r}")


def all_tags(docs: Iterable[Doc]) -> tuple[str, ...]:
    return tuple(sorted({tag for doc in docs for tag in doc.tags}))


def search_docs(docs: Iterable[Doc], term: str = "", tags: Sequence[str] = ()) -> list[Doc]:
    """Case-insensitive substring over title, body and tags, then an AND over tags."""
    needle = term.strip().lower()
    wanted = {t.lower() for t in tags}
    results = []
    for doc in docs:
        haystack = f"{doc.title}\n{doc.body}\n{' '.join(doc.tags)}".lower()
        if needle and needle not in haystack:
            continue
        if wanted and not wanted.issubset({t.lower() for t in doc.tags}):
            continue
        results.append(doc)
    return results
