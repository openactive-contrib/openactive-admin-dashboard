---
name: openactive-colab-notebook
description: How to write an OpenActive analytics notebook that runs in Google Colab and queries BigQuery (openactive-monitor.openactive_analytics) as the signed-in user. Use whenever asked to create, extend or fix a notebook for OpenActive data analysis. Pair with openactive-bigquery-schema (column names) and openactive-data-analysis (what the data means).
---

# Writing an OpenActive analytics notebook for Colab

Team members open these notebooks from GitHub with an "Open in Colab" link and run them in
their own Colab session, signed in with their own Google account.

## Rules

- **Sign in as the user only.** Use `google.colab.auth.authenticate_user()`, as in the setup
  cell below. Never use service accounts, key files, `GOOGLE_APPLICATION_CREDENTIALS` or
  `from_service_account_*`, not even in a comment.
- **Every query goes through `run_query`**, and values are passed as `@name` query
  parameters.
- **Keep scans small.** `opportunities` is ~8M rows and ~10 GB, most of it in `json_data`
  and `inherited_data`. Use the extracted columns (`activity`, `location`, `region_name`,
  `startDate`, …) instead, and check the pre-computed insight tables first
  (openactive-data-analysis).
- **Say the data is from the daily batch.** It is never live.

## Setup cell

The first code cell, copied unchanged apart from `MAX_GB`:

```python
import sys

from google.cloud import bigquery

PROJECT = "openactive-monitor"
DATASET = "openactive-monitor.openactive_analytics"
MAX_GB = 5  # refuse any single query that would scan more than this

if "google.colab" in sys.modules:
    from google.colab import auth

    auth.authenticate_user()  # opens a Google sign-in; queries then run as your account

client = bigquery.Client(project=PROJECT)


def run_query(sql, params=None, max_gb=MAX_GB):
    """Dry-run, report the scan size, then run with a hard cap on bytes billed."""
    config = bigquery.QueryJobConfig(query_parameters=params or [])
    dry = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=params or [], dry_run=True, use_query_cache=False
        ),
    )
    gb = dry.total_bytes_processed / 1e9
    print(f"This query will scan {gb:.2f} GB.")
    if gb > max_gb:
        raise RuntimeError(f"Scan of {gb:.2f} GB exceeds max_gb={max_gb}; narrow the query.")
    config.maximum_bytes_billed = int(max_gb * 1e9)
    return client.query(sql, job_config=config).to_dataframe()
```

The title cell carries this badge, with `<file>` replaced by the notebook's file name:

```markdown
<a href="https://colab.research.google.com/github/openactive-contrib/openactive-admin-dashboard/blob/main/notebooks/<file>" target="_parent"><img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/></a>
```

`notebooks/01_bigquery_starter.ipynb` is a complete example.

## Adding it to this repository

1. Save it as `notebooks/NN_snake_case_title.ipynb`, using the next free two-digit prefix.
2. Add an entry to `notebooks/notebooks.yaml` with `id`, `title`, `file`, `description` and
   `tags`. The dashboard's Notebooks page lists notebooks from there.
3. Clear all outputs.
4. Run `uv run ruff check notebooks && uv run ruff format notebooks` and
   `uv run pytest tests/unit/test_notebooks.py`.
