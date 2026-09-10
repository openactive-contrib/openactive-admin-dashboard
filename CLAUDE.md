# CLAUDE.md — OpenActive Data Stewards Dashboard

Internal Streamlit app for the ODI tech team. Monitors data health/quality for ~170
OpenActive publishers. **Read-only.** Data arrives from a RESTful API over a daily BigQuery
batch — never present it as live.

`BUILD_BRIEF.md` holds the settled product decisions and the full API contract; do not
re-decide anything settled there. `Data Stewards Dashboard.dc.html` is the approved UI
mockup — match its structure, hierarchy and copy tone using native Streamlit widgets (no
custom JS components).

## Current state

**The whole procedure for adding one — registry entry, page, home-page card, API contract, sample payloads,
tests — is `docs/adding-a-dashboard.md`.** `.claude/skills/add-monitor/SKILL.md` is the
agent entry point and points at that doc; keep the procedure in the doc, not in the skill.

**Only part of the backing API exists.** `single_feed_stall` and `feed_ingestion_error` read
the live interim admin API (`/admin/single-feed-stall-incidents` and
`/admin/single-feed-stall-trend`, `/admin/feed-ingestion-error-incidents` and
`/admin/feed-ingestion-error-trend`, `?as_of=` plus `?token=`), and `/admin/summary` is live
too — it sends `null` for the counts its batch
does not compute yet, which the overview shows as "not reported" (see the `/summary`
contract below). `dataset_orphaned_children` reads
`/admin/dataset-orphaned-children-incidents`, which is live, but has **no history at all**:
its `/summary` sparkline is empty and its trend endpoint 404s, so its card is judged on a
declared benchmark rather than on a series (see "Monitor card states"). The app also
requests `/admin/contact-queue`; that is not deployed yet, so it 404s and that page renders
the typed "endpoint is not live" state rather than failing. A missing **trend** costs only
the chart: `repository.fetch_trend_points` swallows the error, so the monitor's own page
still renders its KPIs, filters and table, and the overview falls back to the summary
sparkline. `api/endpoints.py` holds both URL shapes — `contract` (the versioned
`/api/v1/monitors/<id>/...` design) and `admin` — selected by `STEWARDS_API_STYLE`.

Run the app against the live API in dev mode with auth disabled:

```bash
STEWARDS_ENV=dev STEWARDS_DISABLE_AUTH=true uv run streamlit run src/stewards/app.py
```

Also built out: the overview and the cross-monitor contact queue. Runbooks are **not** in
the app: they live in `docs/` and are published to GitHub Pages by
`.github/workflows/pages.yml`. The sidebar's Documentation row is an external
`st.page_link` to `STEWARDS_DOCS_URL`, which opens in a new tab.

## Stack

- Python 3.12, Streamlit ≥ 1.40 (`st.navigation`, `st.login`, `st.dataframe` selections)
- `httpx` for the API client, `pydantic` v2 for response models, `pandas` for table shaping
- `uv` for dependency management, `ruff` for lint+format, `mypy --strict` on `src/`
- `pytest` + `pytest-cov` + `respx` (httpx mocking) for tests

## Layout

```
src/stewards/
  app.py                     settings, auth gate, st.navigation
  config.py                  Settings + load_settings(mapping); Streamlit-free
  auth/google.py             OIDC gate; `decide()` is the pure allowlist decision
  api/client.py              httpx transport: base URL, auth, timeout, retries
  api/endpoints.py           logical read -> path + query, per API shape; Streamlit-free
  api/errors.py              ApiUnavailable | ApiUnauthorized | ApiNotFound | ApiContractError
  api/models.py              pydantic models mirroring the API contract
  api/repository.py          typed function per endpoint (the ONLY caller of client.py)
  monitors/registry.py       Monitor / Col / ColKind / RowSpec / Group / Severity + MONITOR_REGISTRY
  monitors/tile_viz.py       Sparkline | Gauge — what a monitor's overview card draws
  monitors/thresholds.py     Tone, days_tone, is_past_threshold, status/score/risk tones
  monitors/health.py         trend arithmetic -> CRITICAL/WARNING/HEALTHY, per monitor
  monitors/gauge.py          the same verdict from a fixed benchmark, for a monitor with no series
  monitors/transforms.py     incidents -> Rows -> DataFrame, tone frame, KPIs, filters
  monitors/overview.py       tiles, tile state, sidebar labels
  monitors/contact_queue.py  the cross-monitor union, shaped
  monitors/trend.py          30-snapshot series
  monitors/email_draft.py    the publisher email draft
  components/…               theme, surface, layout, nav, filters, incident_table,
                             trend_chart, email_draft, errors, monitor_page,
                             overview_page, contact_queue_page
  views/…                    one 3-line module per page, zero logic
                             (NOT `pages/` — see hard rule 8)
tests/
  unit/                      pure logic: thresholds, registry, transforms, email
  contract/                  respx-backed client + repository tests
  smoke/                     AppTest renders of every page and every error state
  fixtures/                  test-only payload variants (empty, malformed, paginated)
```

## Hard rules

1. **Pages contain no logic.** A page module calls one `render_*_page` component and nothing
   else. All shaping, filtering, derivation and formatting lives in pure functions under
   `monitors/` — that is what the unit tests exercise.
2. **No Streamlit imports in testable logic.** Anything that computes a value must be
   importable and callable without a Streamlit runtime. `monitors/` and
   `config.py` hold that line; `api/repository.py` is the boundary where `st.cache_data`
   starts. If a function needs `st`, it belongs in a component and must be a thin renderer.
3. **No raw dicts past the client boundary.** `api/client.py` returns parsed JSON;
   `api/repository.py` returns pydantic models. Pages and components see models or
   DataFrames. `Incident.detail` is the one untyped field, and it is only ever read through
   the `detail_model` its monitor declares — never with a string key in a page. `Incident`
   folds top-level keys it does not declare into `detail`, so a monitor whose batch reports
   its measurements un-nested still reaches them through that typed path.
4. **Adding a monitor must not touch shared code.** One registry entry + one page stub +
   sample payloads + one test module. If a new monitor forces an edit to `transforms.py` or
   `incident_table.py`, generalise the component instead of special-casing.
5. **Read-only.** No mute, assign, re-crawl, or send-email actions. A copyable email
   draft is the only output. CSV export was removed from the header on request — do not
   reintroduce a download button without being asked.
6. **Every data page shows the snapshot timestamp** from the API `meta.snapshot_date`, via
   `components.layout.render_header`, which is the whole header bar (crumb, title,
   snapshot). A page that failed to load renders `render_error_header`
   instead — no snapshot line, because there is no snapshot.
7. Secrets only via `.streamlit/secrets.toml` / env. Never commit tokens, never log the API
   token, never log or display a full user email (`auth.google.mask_email`).
8. **The page modules live in `views/`, never `pages/`.** A folder named `pages` beside the
   entrypoint switches Streamlit into v1 multipage mode, where every page file becomes its
   own entrypoint — a deep link then runs the page script directly and never executes
   `app.py`, silently bypassing the auth gate. `tests/smoke/test_pages.py` guards this.

## Configuration

Env vars, or a `[stewards]` section in `.streamlit/secrets.toml` (env wins). See
`.streamlit/secrets.toml.example`.

| Variable | Meaning |
|---|---|
| `STEWARDS_API_BASE_URL` | Required live API base URL |
| `STEWARDS_API_TOKEN` | Token for the API; not the user's identity |
| `STEWARDS_API_STYLE` | `contract` (default) or `admin` — which URL shape the deployment speaks |
| `STEWARDS_API_TOKEN_PARAM` | Query parameter the token rides in; empty (default) means a bearer header |
| `STEWARDS_ENV` | `prod` (default) or `dev` |
| `STEWARDS_CONTACT_THRESHOLD_DAYS` | Contact threshold, default 7 |
| `STEWARDS_ALLOWED_DOMAIN` | Google workspace allowlist, default `theodi.org` |
| `STEWARDS_DOCS_URL` | Runbooks site the sidebar links out to, default the project's GitHub Pages URL |
| `STEWARDS_DISABLE_AUTH` | Skip the auth gate; honoured **only** when `STEWARDS_ENV=dev` |

## Monitor card states

A card's visualisation is declared per monitor — `Monitor.viz` is a `Sparkline` (the
default) or a `Gauge` — and `components.overview_page.tile_chart` dispatches on it. Adding a
third is one variant in `monitors/tile_viz.py`, one builder returning `alt.Chart | None`, and
one `case`; switching a card between them is one line in its registry entry.

`CRITICAL` / `WARNING` / `HEALTHY` / `NO DATA` on an overview card is computed from the
monitor's own daily series, not configured: `monitors/health.py` runs a Theil-Sen slope
(relative to the series' own level, so it is scale-free), a tie-corrected Mann-Kendall
p-value and an Iglewicz-Hoaglin modified z-score for a step change, and the past-threshold
series escalates on top. The overview therefore reads every monitor's trend
(`repository.fetch_monitor_trends`) and falls back to the `/summary` sparkline for a monitor
whose trend endpoint is not deployed. Which way is bad is per monitor: `Monitor.health` is a
`HealthPolicy`, and a monitor whose figure is a volume rather than a fault count declares
`Direction.DOWN_IS_BAD` — every rule runs on the oriented series, so there is no second code
path for it.

A monitor with **no series at all** is the one exception: `viz=Gauge(benchmark=…)` makes
`monitors/gauge.assess_benchmark` produce the verdict from that benchmark instead, returning
the same `Health` type so the chip, tone and sidebar pill run through unchanged code. Its
`movement` is always unknown and its `points` zero, so the card claims no trend it cannot
support. `docs/adding-a-dashboard.md` §7 "Card state" is the full account.

## The `/summary` contract

Every count in `BUILD_BRIEF.md` §3 is `int | None`. A deployment sends `null` for a figure
its batch does not compute for that snapshot — the live admin API does exactly this for
`open_incidents` and `past_threshold` — and null is not zero: the KPI reads em dash with no
tone, the tile says "not reported", and the sidebar badge is omitted.
`monitors.overview.format_count` owns that rendering. The same applies per monitor inside
`data.monitors` (`count`, `past_threshold_count`, and null points in `sparkline`).

Beyond those counts, `/summary` may send optional `publishers_with_issues_delta`,
`open_incidents_delta` and `past_threshold_delta` (change against the previous snapshot).
They are `int | None` too: absent means the KPI renders with no delta, never a fabricated
zero. `monitors.overview.format_delta` owns the sign convention.

## Testing bar

- `pytest` must pass. Coverage on `src/stewards/{monitors,components,api}` ≥ 90%, project
  ≥ 80%. Currently 100% / 99% / 99% and 99% overall.
- Every pure function gets: a happy path, an empty-input case, and one boundary case
  (`days_open == threshold`, zero rows, null score, missing optional field).
- API client tested with `respx` against fixtures — including 401, 500, a timeout, a
  malformed payload and a two-page paginated response. Never hit the network in tests.
- Threshold arithmetic has its own module, `tests/unit/test_thresholds.py`; it is the logic
  most likely to be quietly wrong.
- `tests/unit/test_health.py` owns the trend arithmetic, the other logic most likely to be
  quietly wrong: every rule is asserted at its boundary and the direction parameter is
  asserted to be a mirror of itself rather than a second code path.
- `tests/unit/test_registry.py` parametrises over the whole registry, so every future
  monitor is validated for free — ids, page module, sample payload, resolvable column and
  filter fields, detail model.
- Smoke tests use `AppTest`. `AppTest.from_function` re-executes the function's own source,
  so such a script must import everything it uses and annotate its parameters with builtins
  only (`exc: object`) — a quoted annotation gets unquoted again by ruff's UP037 fix.
- Add a regression test with the bug's payload whenever you fix a data bug.

## Commands

```bash
uv sync --extra dev                                     # install
uv run streamlit run src/stewards/app.py                # see "Current state" for the flags
uv run pytest -q --cov=src/stewards --cov-report=term-missing
uv run ruff check --fix . && uv run ruff format .
uv run mypy src
```

## Conventions

- Type hints everywhere; `from __future__ import annotations`.
- Docstrings only where behaviour is non-obvious — no restating the signature.
- Semantic colours are fixed: red `#C6413B`, amber `#C77F1A`, green `#1F7A4C`,
  grey `#5C6B76`, teal `#0E8F8A` (primary). Defined once in `components/theme.py`; use
  `theme.markdown_colour(tone)` for coloured text rather than inline HTML.
- Copy tone: factual, no exclamation marks, no emoji in UI text. Dates are ISO
  everywhere they describe data (snapshots, incidents). The runbooks on GitHub Pages are
  outside the app and set their own conventions.
- Cache API reads with `@st.cache_data(ttl=3600)` at the repository layer only; the wrapped
  `_fetch_*` function stays cache-free so tests call it directly.
- No page or component builds a URL. `api/endpoints.py` maps each of the four logical reads
  onto a path and query per shape; both shapes route all four, and an endpoint a deployment
  has not built yet answers 404, which becomes `ApiNotFound` on the page that needs it.
- Filtering, searching and sorting happen locally over the cached snapshot, not as API query
  params, so the controls respond without a refetch and stay unit-testable.
- The full brand palette lives in `components/theme.py`; `.streamlit/config.toml` mirrors it
  onto Streamlit's own tokens (including `[theme.sidebar]` for the dark sidebar and the
  red/orange/yellow/green/gray/blue slots that back `:red[…]` and the alert boxes).
  `tests/unit/test_theme.py` fails if the two drift apart, so change both or neither, and
  it also fails on any hex inlined outside `theme.py`.
- The page background is the canvas tint; cards are white. Streamlit has no theme token for
  a container's fill, nor a per-element type scale, so `components/surface.py` holds the
  app's **only** stylesheet: card fill plus the header-bar and KPI type scale. Cards opt in
  with `card("name")` — never a bare `st.container(border=True)`. Elements hook the type
  scale through container **keys** (`st-key-*`), never a generated emotion class, and no
  data is ever interpolated into markup.
- `layout.tone_metric(label, value, tone, slug=…, delta=…, sub=…)` renders every KPI. Its
  `slug` must be unique on the page — it becomes the container key. `tone=None` leaves the
  value in body ink, for a figure that is context rather than a state.
- Streamlit's built-in sidebar nav takes a plain-text label, so it is hidden
  (`st.navigation(..., position="hidden")`) and `components/nav.render_sidebar` draws the
  grouped sidebar with `st.page_link` plus an `st.badge` count pill per item.
- Charts are Altair, built by `monitors/trend.py` and passed their colours by the caller:
  `st.line_chart` cannot draw a dashed series or a transparent plot area. Tile sparklines
  are axis-less; the trend chart is solid teal over dashed red.
- Use `width="stretch"` / `width="content"`. `use_container_width` is past its removal date.
- Route URLs drop the filename's numeric prefix: `views/12_feed_ingestion_errors.py` serves
  `/feed_ingestion_errors`.
- `layout.render_header` requires a `Meta`: every page it serves is backed by the daily
  batch, so the snapshot line is never optional.

## Before you finish a task

Run lint, mypy and the full test suite. State what you did not test and why.
