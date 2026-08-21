# CLAUDE.md — OpenActive Data Stewards Dashboard

Internal Streamlit app for the ODI tech team. Monitors data health/quality for ~170
OpenActive publishers. **Read-only.** Data arrives from a RESTful API over a daily BigQuery
batch — never present it as live.

`BUILD_BRIEF.md` holds the settled product decisions and the full API contract; do not
re-decide anything settled there. `Data Stewards Dashboard.dc.html` is the approved UI
mockup — match its structure, hierarchy and copy tone using native Streamlit widgets (no
custom JS components).

## Current state

Two of the eight monitors in the brief are built: `single_feed_stall` and `http_failure`.
The remaining six land one at a time as their API endpoints go live — see
`.claude/skills/add-monitor/SKILL.md`, which is the whole procedure.

**The backing API does not exist yet.** The app is a finished client against the contract in
`BUILD_BRIEF.md` §3, and it ships sample payloads so it can be run and reviewed today:

```bash
STEWARDS_USE_SAMPLE_DATA=true STEWARDS_ENV=dev STEWARDS_DISABLE_AUTH=true \
  uv run streamlit run src/stewards/app.py
```

The payloads in `src/stewards/api/sample_data/` are served through an `httpx.MockTransport`
and are also the happy-path contract fixtures for the tests — one copy of each shape. When
the real API lands, point `STEWARDS_API_BASE_URL` at it and drop the flags; nothing else
changes. Any page in sample-data mode renders a banner saying so.

Also built out: the overview, the cross-monitor contact queue, and the knowledge base (one
seed document — the single-feed stalls runbook).

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
  api/client.py              httpx transport: base URL, auth header, timeout, retries
  api/errors.py              ApiUnavailable | ApiUnauthorized | ApiNotFound | ApiContractError
  api/models.py              pydantic models mirroring the API contract
  api/repository.py          typed function per endpoint (the ONLY caller of client.py)
  api/sample_transport.py    MockTransport serving sample_data/ before the API exists
  api/sample_data/*.json     bundled payloads, one per endpoint
  monitors/registry.py       Monitor / Col / ColKind / Group / Severity + MONITOR_REGISTRY
  monitors/thresholds.py     Tone, days_tone, is_past_threshold, status/score tones
  monitors/transforms.py     incidents -> DataFrame, tone frame, KPIs, filters, CSV
  monitors/overview.py       tiles, tile state, sidebar labels
  monitors/contact_queue.py  the cross-monitor union, shaped
  monitors/trend.py          30-snapshot series
  monitors/email_draft.py    the publisher email draft
  knowledge/loader.py        markdown front matter, search, tags, headings
  components/…               theme, surface, layout, nav, filters, incident_table,
                             trend_chart, email_draft, errors, monitor_page,
                             overview_page, contact_queue_page, docs_page
  views/…                    one 3-line module per page, zero logic
                             (NOT `pages/` — see hard rule 8)
  docs/*.md                  knowledge base content, with front matter
tests/
  unit/                      pure logic: thresholds, registry, transforms, email, docs
  contract/                  respx-backed client + repository tests
  smoke/                     AppTest renders of every page and every error state
  fixtures/                  test-only payload variants (empty, malformed, paginated)
```

## Hard rules

1. **Pages contain no logic.** A page module calls one `render_*_page` component and nothing
   else. All shaping, filtering, derivation and formatting lives in pure functions under
   `monitors/` or `knowledge/` — that is what the unit tests exercise.
2. **No Streamlit imports in testable logic.** Anything that computes a value must be
   importable and callable without a Streamlit runtime. `monitors/`, `knowledge/` and
   `config.py` hold that line; `api/repository.py` is the boundary where `st.cache_data`
   starts. If a function needs `st`, it belongs in a component and must be a thin renderer.
3. **No raw dicts past the client boundary.** `api/client.py` returns parsed JSON;
   `api/repository.py` returns pydantic models. Pages and components see models or
   DataFrames. `Incident.detail` is the one untyped field, and it is only ever read through
   the `detail_model` its monitor declares — never with a string key in a page.
4. **Adding a monitor must not touch shared code.** One registry entry + one page stub +
   sample payloads + one test module. If a new monitor forces an edit to `transforms.py` or
   `incident_table.py`, generalise the component instead of special-casing.
5. **Read-only.** No mute, assign, re-crawl, or send-email actions. Export CSV and a
   copyable email draft are the only outputs.
6. **Every data page shows the snapshot timestamp** from the API `meta.snapshot_date`, via
   `components.layout.render_header`, which is the whole header bar (crumb, title,
   snapshot, Export CSV). A page that failed to load renders `render_error_header`
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
| `STEWARDS_API_BASE_URL` | Required unless sample-data mode is on |
| `STEWARDS_API_TOKEN` | Bearer token for the API; not the user's identity |
| `STEWARDS_ENV` | `prod` (default) or `dev` |
| `STEWARDS_CONTACT_THRESHOLD_DAYS` | Contact threshold, default 7 |
| `STEWARDS_ALLOWED_DOMAIN` | Google workspace allowlist, default `theodi.org` |
| `STEWARDS_USE_SAMPLE_DATA` | Serve the bundled payloads instead of calling the API |
| `STEWARDS_DISABLE_AUTH` | Skip the auth gate; honoured **only** when `STEWARDS_ENV=dev` |

## The `/summary` contract

Beyond the counts in `BUILD_BRIEF.md` §3, `/summary` may send optional
`publishers_with_issues_delta`, `open_incidents_delta` and `past_threshold_delta`
(change against the previous snapshot). They are `int | None`: absent means the KPI
renders with no delta, never a fabricated zero. `monitors.overview.format_delta` owns the
sign convention.

## Testing bar

- `pytest` must pass. Coverage on `src/stewards/{monitors,components,api}` ≥ 90%, project
  ≥ 80%. Currently 100% / 98% / 99% and 98% overall.
- Every pure function gets: a happy path, an empty-input case, and one boundary case
  (`days_open == threshold`, zero rows, null score, missing optional field).
- API client tested with `respx` against fixtures — including 401, 500, a timeout, a
  malformed payload and a two-page paginated response. Never hit the network in tests.
- Threshold arithmetic has its own module, `tests/unit/test_thresholds.py`; it is the logic
  most likely to be quietly wrong.
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
- Copy tone: factual, no exclamation marks, no emoji in UI text.
- Cache API reads with `@st.cache_data(ttl=3600)` at the repository layer only; the wrapped
  `_fetch_*` function stays cache-free so tests call it directly.
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
- Route URLs drop the filename's numeric prefix: `views/12_http_failures.py` serves
  `/http_failures`.

## Before you finish a task

Run lint, mypy and the full test suite. State what you did not test and why.
