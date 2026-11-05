"""The notebook manifest behind the Notebooks page. Streamlit-free.

`notebooks/notebooks.yaml` at the repository root lists the example notebooks. Each one opens
in Google Colab straight from GitHub, so the page needs only the manifest at runtime — the
Docker image ships that file and none of the notebooks themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

#: The folder's name in the repository, which is also its path in every GitHub URL.
NOTEBOOKS_DIR_NAME = "notebooks"

#: Resolved against the package rather than the working directory: `src/stewards/` sits two
#: levels below the repository root locally and in the image (`/app/src/stewards`).
REPO_ROOT = Path(__file__).resolve().parents[2]
NOTEBOOKS_DIR = REPO_ROOT / NOTEBOOKS_DIR_NAME
MANIFEST_PATH = NOTEBOOKS_DIR / "notebooks.yaml"
README_NAME = "README.md"

COLAB_GITHUB = "https://colab.research.google.com/github"
GITHUB = "https://github.com"


class ManifestError(RuntimeError):
    """The manifest is missing, is not YAML, or does not match the expected shape."""


class Notebook(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    file: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.ipynb$")
    """A file name inside `notebooks/` — no subfolders, so no path can climb out of it."""
    description: str = ""
    tags: tuple[str, ...] = ()


class NotebookManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    """`owner/name` on GitHub."""
    branch: str = Field(default="main", min_length=1)
    notebooks: tuple[Notebook, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [notebook.id for notebook in self.notebooks]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate notebook ids: {', '.join(duplicates)}")
        return self

    def _repo_path(self, file: str) -> str:
        return f"{self.repository}/blob/{self.branch}/{NOTEBOOKS_DIR_NAME}/{file}"

    def colab_url(self, notebook: Notebook) -> str:
        return f"{COLAB_GITHUB}/{self._repo_path(notebook.file)}"

    def github_url(self, notebook: Notebook) -> str:
        return f"{GITHUB}/{self._repo_path(notebook.file)}"

    def readme_url(self) -> str:
        return f"{GITHUB}/{self._repo_path(README_NAME)}"


def parse_manifest(text: str) -> NotebookManifest:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("expected a mapping with `repository` and `notebooks`")
    try:
        return NotebookManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(str(exc)) from exc


def load_manifest(path: Path = MANIFEST_PATH) -> NotebookManifest:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read {path.name}: {exc.strerror}") from exc
    return parse_manifest(text)
