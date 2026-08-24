# OpenActive Data Stewards Dashboard

Internal Streamlit app for the ODI tech team to monitor the health and quality of data
published by the ~170 OpenActive publishers. Read-only, behind Google SSO, restricted to the
`theodi.org` workspace.

Data comes from the stewards REST API, which fronts a **daily BigQuery batch** — every page
states the snapshot date rather than implying live data.

## Run it

```bash
uv sync --extra dev
```

The backing API is not built yet, so the app ships sample payloads and can be run today:

```bash
STEWARDS_USE_SAMPLE_DATA=true STEWARDS_ENV=dev STEWARDS_DISABLE_AUTH=true \
  uv run streamlit run src/stewards/app.py
```

Against the real API, copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml`, fill in the Google OIDC client and the API token, and run:

```bash
uv run streamlit run src/stewards/app.py
```

## Develop

```bash
uv run pytest -q --cov=src/stewards --cov-report=term-missing
uv run ruff check --fix . && uv run ruff format .
uv run mypy src
```

## CI

`.github/workflows/ci.yml` runs on every pull request to `main`, and on pushes to `main`:

- **Lint** — `ruff check`, `ruff format --check`, `mypy --strict`
- **Tests** — `pytest`, with the coverage bars enforced rather than aspirational:
  >= 80% project-wide, and >= 90% across `monitors/`, `components/` and `api/`

Dependencies install with `uv sync --extra dev --locked`, so a `pyproject.toml` change
committed without a refreshed `uv.lock` fails the build instead of silently resolving to
something the lockfile does not describe.

## Where things are

- `BUILD_BRIEF.md` — settled product decisions, page map, API contract, visual language
- `CLAUDE.md` — architecture, hard rules, configuration, testing bar
- `.claude/skills/add-monitor/SKILL.md` — how to add the next monitor
- `Data Stewards Dashboard.dc.html` — the approved UI mockup

Two of the eight monitors are built (`single_feed_stall`, `http_failure`), plus the overview,
and the cross-monitor contact queue. The rest follow their API endpoints. Runbooks are
published separately from `docs/` to GitHub Pages.
