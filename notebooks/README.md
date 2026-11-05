# Notebooks

Example notebooks for the data stewards team. They run in free Google Colab, opened from
GitHub through an "Open in Colab" link — the dashboard's **Reference → Notebooks** page lists
every one of them.

## Signing in to BigQuery

A notebook that reads BigQuery signs in with the user's own Google account. Its setup cell calls
`google.colab.auth.authenticate_user()`, which asks the user to pick their Google account. The
queries then run as that email, so access is whatever the account has been granted: read
access to `openactive-monitor.openactive_analytics`, and permission to run query jobs in the
`openactive-monitor` project (`roles/bigquery.dataViewer` on the dataset and
`roles/bigquery.jobUser` on the project). `01_bigquery_starter.ipynb` is the quickest way to
check someone's access.

No service accounts, key files or credentials of any kind are committed here or read from
anywhere. `tests/unit/test_notebooks.py` fails if a notebook mentions one.

## Writing notebooks with an LLM

`skills/` holds three skills. Give all three to the model and ask for the analysis you want.

| Skill | What it gives the model |
|---|---|
| `openactive-colab-notebook` | How to build the notebook: Colab sign-in, the setup cell with a cost guard, structure, charts, and how to add it to this repository |
| `openactive-bigquery-schema` | Where the table and column definitions live: links to the schema files in `openactive-contrib/openactive-monitor`, plus a live `INFORMATION_SCHEMA` fallback. Nothing is copied, so nothing goes stale |
| `openactive-data-analysis` | What the data means: kinds, parent/child rows, "future", regular vs preview, the JSON column shapes, geography, and the pre-computed insight tables |

Each one is a folder with a `SKILL.md` in the standard Agent Skills format.

- **Claude Code**, working in this repository: ask it to read the three `SKILL.md` files
  first, or copy the folders into `.claude/skills/` (or `~/.claude/skills/`) so it picks them
  up on its own.
- **Claude apps**: zip each folder and upload it under Settings → Capabilities → Skills.
- **Other assistants**: paste the three `SKILL.md` files into the conversation, or attach them.

The schema skill tells the model to fetch the schema files from GitHub. A model without web
access has to be given them: attach
`openactive-monitor/.claude/skills/bigquery-schemas/SKILL.md`, and the
`jobs/opportunity-insights/schemas/<table>.json` file for any insight table you want used.

## Adding a notebook

1. Add the `.ipynb` file to this folder. Clear its outputs before committing — they can carry
   query results, and they make diffs unreadable.
2. Add an entry to `notebooks.yaml`: a unique snake_case `id`, a `title`, the `file` name,
   a one- or two-sentence `description`, and optional `tags`.
3. Merge to `main`. The Colab link opens the notebook from `main` on GitHub, so it 404s
   until then.

`tests/unit/test_notebooks.py` checks that every notebook in this folder is listed in the
manifest, every manifest entry has its file, and no notebook is committed with outputs.
