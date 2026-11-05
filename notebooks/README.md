# Notebooks

Example notebooks for the data stewards team. They run in free Google Colab, opened from
GitHub through an "Open in Colab" link — the dashboard's **Reference → Notebooks** page lists
every one of them.

## Signing in to BigQuery

A notebook that reads BigQuery signs in with the user's own Google account. Its first code
cell is:

```python
from google.colab import auth

auth.authenticate_user()
```

followed by a client built for the project the user has access to:

```python
from google.cloud import bigquery

client = bigquery.Client(project="<gcp-project-id>")
```

No service accounts, key files or credentials of any kind are committed here or read from
anywhere. `tests/unit/test_notebooks.py` fails if a notebook mentions one.

## Adding a notebook

1. Add the `.ipynb` file to this folder. Clear its outputs before committing — they can carry
   query results, and they make diffs unreadable.
2. Add an entry to `notebooks.yaml`: a unique snake_case `id`, a `title`, the `file` name,
   a one- or two-sentence `description`, and optional `tags`.
3. Merge to `main`. The Colab link opens the notebook from `main` on GitHub, so it 404s
   until then.

`tests/unit/test_notebooks.py` checks that every notebook in this folder is listed in the
manifest, every manifest entry has its file, and no notebook is committed with outputs.
