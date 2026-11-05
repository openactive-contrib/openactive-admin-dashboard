"""The notebook manifest, and the notebooks folder it describes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from stewards.notebooks import (
    MANIFEST_PATH,
    NOTEBOOKS_DIR,
    ManifestError,
    load_manifest,
    parse_manifest,
)

MANIFEST = """
repository: openactive-contrib/openactive-admin-dashboard
branch: main
notebooks:
  - id: hello_colab
    title: Hello from Colab
    file: 00_hello_colab.ipynb
    description: A demo.
    tags: [demo]
"""


def test_a_manifest_parses_into_notebooks() -> None:
    manifest = parse_manifest(MANIFEST)
    [notebook] = manifest.notebooks
    assert notebook.id == "hello_colab"
    assert notebook.title == "Hello from Colab"
    assert notebook.tags == ("demo",)


def test_the_colab_link_opens_the_notebook_from_github() -> None:
    manifest = parse_manifest(MANIFEST)
    [notebook] = manifest.notebooks
    assert manifest.colab_url(notebook) == (
        "https://colab.research.google.com/github/openactive-contrib/"
        "openactive-admin-dashboard/blob/main/notebooks/00_hello_colab.ipynb"
    )
    assert manifest.github_url(notebook) == (
        "https://github.com/openactive-contrib/openactive-admin-dashboard"
        "/blob/main/notebooks/00_hello_colab.ipynb"
    )


def test_the_readme_link_points_at_the_folder_readme_on_the_manifest_branch() -> None:
    manifest = parse_manifest("repository: o/r\nbranch: dev\n")
    assert manifest.readme_url() == "https://github.com/o/r/blob/dev/notebooks/README.md"


def test_the_branch_defaults_to_main_and_optional_fields_to_empty() -> None:
    manifest = parse_manifest(
        "repository: o/r\nnotebooks:\n  - {id: a, title: A, file: a.ipynb}\n"
    )
    assert manifest.branch == "main"
    assert manifest.notebooks[0].description == ""
    assert manifest.notebooks[0].tags == ()


def test_a_manifest_with_no_notebooks_is_valid() -> None:
    assert parse_manifest("repository: o/r\n").notebooks == ()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "mapping"),
        ("- just a list", "mapping"),
        ("repository: [unclosed", "YAML"),
        ("notebooks: []", "repository"),
        ("repository: o/r\nnotebooks:\n  - {id: a, file: a.ipynb}", "title"),
        ("repository: o/r\nnotebooks:\n  - {id: a, title: A, file: ../a.ipynb}", "file"),
        ("repository: o/r\nnotebooks:\n  - {id: a, title: A, file: a.py}", "file"),
        ("repository: o/r\nnotebooks:\n  - {id: A b, title: A, file: a.ipynb}", "id"),
        ("repository: o/r\nunknown: 1", "unknown"),
        (
            "repository: o/r\nnotebooks:\n"
            "  - {id: a, title: A, file: a.ipynb}\n  - {id: a, title: B, file: b.ipynb}",
            "duplicate notebook ids: a",
        ),
    ],
)
def test_a_malformed_manifest_raises_manifest_error(text: str, reason: str) -> None:
    with pytest.raises(ManifestError, match=re.escape(reason)):
        parse_manifest(text)


def test_a_missing_manifest_raises_manifest_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="cannot read"):
        load_manifest(tmp_path / "notebooks.yaml")


# --- the committed folder ------------------------------------------------------------------


def test_the_committed_manifest_loads() -> None:
    assert load_manifest(MANIFEST_PATH).notebooks


def test_every_manifest_entry_has_its_notebook_and_every_notebook_is_listed() -> None:
    listed = {notebook.file for notebook in load_manifest().notebooks}
    on_disk = {path.name for path in NOTEBOOKS_DIR.glob("*.ipynb")}
    assert listed == on_disk


NOTEBOOKS = sorted(NOTEBOOKS_DIR.glob("*.ipynb"))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_notebooks_are_committed_without_outputs(path: Path) -> None:
    """Outputs can carry query results; they stay in the user's Colab session."""
    notebook = json.loads(path.read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == []
            assert cell["execution_count"] is None


#: Anything that would mean a notebook signs in as something other than the user.
CREDENTIAL_MARKERS = re.compile(
    r"service_account|private_key|GOOGLE_APPLICATION_CREDENTIALS", re.IGNORECASE
)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_notebooks_sign_in_as_the_user_never_with_a_key(path: Path) -> None:
    assert not CREDENTIAL_MARKERS.search(path.read_text(encoding="utf-8"))


# --- the LLM skills ----------------------------------------------------------------------

SKILLS_DIR = NOTEBOOKS_DIR / "skills"


@pytest.mark.parametrize(
    "path", sorted(SKILLS_DIR.glob("*/SKILL.md")), ids=lambda p: p.parent.name
)
def test_each_skill_names_itself_after_its_folder(path: Path) -> None:
    """The frontmatter `name` is how an agent finds a skill; it must match the folder."""
    header = path.read_text(encoding="utf-8").split("---")[1]
    assert f"name: {path.parent.name}\n" in header
    assert "description: " in header


def test_the_starter_notebook_uses_the_setup_cell_the_skill_prescribes() -> None:
    """The skill tells an LLM to copy its setup cell unchanged; the starter is the example."""
    skill = (SKILLS_DIR / "openactive-colab-notebook" / "SKILL.md").read_text(encoding="utf-8")
    prescribed = skill.split("## Setup cell")[1].split("```python\n")[1].split("```")[0]
    starter = json.loads((NOTEBOOKS_DIR / "01_bigquery_starter.ipynb").read_text("utf-8"))
    first_code = next(c for c in starter["cells"] if c["cell_type"] == "code")
    assert "".join(first_code["source"]).strip() == prescribed.strip()
