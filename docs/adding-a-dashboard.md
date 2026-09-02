---
title: Adding a dashboard
tags: [contributing, monitors, dashboard]
owner: Data Infrastructure
updated: 2026-09-02
sensitivity: internal
---

How to add a new monitor dashboard to the Data Stewards app: its own page, a card on the
home page, a sidebar badge, tests, and either a live API endpoint or bundled sample data so
the page can be reviewed before the endpoint exists.

This page is the whole procedure. It is written to be followed end to end by a person or by
an agent, in order, without reading the rest of the codebase first.

## What a dashboard is here

Every dashboard in this app is a **monitor**: a named check that produces **incidents**. An
incident is one publisher (usually one feed) failing that check, carrying an age in days and
a status. Everything a monitor page shows — header, blurb, three KPIs, trend chart, filters,
RAG-shaded table, email draft — is rendered by shared components driven off a single
registry entry.

So adding a dashboard is **not** writing a page. It is declaring a monitor and supplying its
data. The complete file list, for any new monitor:

| File | Change |
|---|---|
| `src/stewards/monitors/registry.py` | one `Monitor(...)` entry, appended to `MONITOR_REGISTRY` — the whole declaration |
| `src/stewards/views/NN_<name>.py` | three-line page stub |
| `src/stewards/api/sample_data/<id>_incidents.json` | new: payload for sample-data mode, and the test fixture |
| `src/stewards/api/sample_data/<id>_trend.json` | new: 30-snapshot series for the chart |
| `src/stewards/api/sample_data/summary.json` | append a `MonitorCount` for the new id — this is what makes the home-page card and the sidebar badge real |
| `tests/unit/test_<id>.py` | happy path, empty input, threshold boundary |
| `src/stewards/api/models.py` *(only if the monitor has `detail` fields)* | a `DetailModel` subclass |
| `src/stewards/monitors/email_draft.py` *(optional)* | a check-specific observation sentence |

Plus the fleet-wide test expectations listed in step 10, which a new monitor changes by
definition.

If a new monitor forces an edit to `monitors/transforms.py`, `components/incident_table.py`
or any other shared module, that is a signal the shared code is not general enough.
Generalise the component instead of special-casing the monitor. That rule is what keeps
eight dashboards from becoming eight one-off pages.

## 1. Decide the monitor's shape

Settle these before writing anything. Every one of them is a field on the registry entry.

| Decision | Field | Notes |
|---|---|---|
| Machine id | `id` | snake_case. **It is the API path segment**: `/monitors/<id>/incidents`. It also names the sample payloads and keys the sidebar badge. |
| Display name | `name` | Sentence case, e.g. `Zero future opportunities`. Used on the page, the tile and the sidebar. |
| Group | `group` | `Group.AVAILABILITY`, `Group.CONTENT` or `Group.COVERAGE`. Sets the sidebar section and the page breadcrumb (`"<group> monitor"`). |
| Severity | `severity` | `CRITICAL`, `HIGH`, `MEDIUM`, `INFORMATIONAL`. Only `INFORMATIONAL` changes behaviour: its tile stays grey while non-zero instead of going amber. |
| Blurb | `blurb` | 2–3 factual sentences: what the check looks at, and what it deliberately excludes. Must be longer than 80 characters and contain no exclamation mark (asserted). |
| Unit noun | `unit` | The noun under the tile count, e.g. `feeds at zero`. |
| Columns | `columns` | The table, in order. See the column kinds below. |
| Detail fields | `detail_model` | The typed view of `Incident.detail`. Omit for a monitor with no extra fields. |
| Contact threshold | `threshold_days` | Days before an incident is contact-due. Default 7. `days_open == threshold_days` **is** past threshold. |
| Filters | `filters` | One selectbox per `FilterSpec`; options are the distinct values in the snapshot. |
| KPI labels | `kpi_labels` | Three labels: count, publishers affected, past threshold. |
| Queue summary field | `summary_field` | Which field identifies the row in the cross-monitor contact queue. Defaults to `feed_name`. |
| Threshold toggle | `has_threshold_filter` | `True` (default) adds the "Past threshold only" toggle to the filter row. Set `False` for an informational monitor where the threshold means nothing. |
| Schedule | `schedule` | Free text shown as a meta chip, e.g. `daily 04:00 UTC · suppress 1 day`. |
| Query name | `query` | The BigQuery/API query identifier, shown in the page footer for provenance. |
| Page module | `page` | `views/NN_<name>.py`, relative to `src/stewards/`. |

`Monitor.extras` also exists but is not consumed by any component today. Leave it empty.

### Column kinds

`ColKind` decides both the formatting and the `st.column_config` widget. Never write
`column_config` in a page.

| Kind | Renders as | RAG-shaded |
|---|---|---|
| `TEXT` | plain text, em dash when empty | no |
| `MONO` | plain text (monospace intent) | no |
| `NUMBER` | integer, `NumberColumn` | no |
| `DATE` | ISO date | no |
| `DAYS` | `12d`, coloured against `threshold_days` | yes |
| `PERCENT` / `SCORE` | `ProgressColumn` 0–100 | yes (green ≥ 80, amber ≥ 60, else red) |
| `SPARKLINE` | `LineChartColumn` from `Incident.trend` | no |
| `STATUS` | humanised status label | yes |
| `LINK` | `LinkColumn` showing `feed ↗` | no |

The first column must have `primary=True` (asserted), and column labels must be unique
within a monitor (asserted).

## 2. The API contract

The app is a **client only** — no BigQuery SDK, no SQL, no direct database access. All four
endpoints are already implemented generically in `api/repository.py`, so a new monitor
normally needs **no new client code**: it reuses `/monitors/{id}/incidents` and
`/monitors/{id}/trend` with its own id.

Base URL is `${STEWARDS_API_BASE_URL}/api/v1`.

| Endpoint | Used by | Repository function |
|---|---|---|
| `GET /summary` | overview KPIs, home-page cards, sidebar badges | `fetch_summary()` |
| `GET /monitors/{id}/incidents?page=1&page_size=500` | the monitor page table | `fetch_incidents(id)` |
| `GET /monitors/{id}/trend?days=30` | the monitor page chart | `fetch_trend(id)` |
| `GET /contact-queue` | the cross-monitor queue | `fetch_contact_queue()` |

Every response is an envelope: `{"data": ..., "meta": {...}}`.

```json
"meta": {
  "snapshot_date": "2026-08-21",
  "generated_at": "2026-08-21T06:12:04Z",
  "total": 9,
  "page": 1,
  "page_size": 500
}
```

`meta.snapshot_date` is mandatory on every data endpoint: every page states the snapshot it
was built from, because the data is a daily batch and must never read as live.

### Incidents

`data` is an array of incidents. Unknown keys are ignored; every optional field may be
`null`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `monitor_id` | string | yes | Must equal the registry `id` (asserted against the payload). |
| `publisher_id` | string | yes | Identity for the distinct-publisher KPI. |
| `publisher_name` | string | yes | Searchable. |
| `first_detected` | date | yes | ISO. |
| `days_open` | int | yes | The API owns this figure; the app only classifies it. |
| `past_threshold` | bool | yes | The API owns this too. The app does not recompute it for filtering or KPIs. |
| `status` | string | yes | See the status vocabulary below. |
| `feed_id`, `feed_name`, `feed_type`, `feed_url` | string | no | Feed-level monitors set these; publisher-level ones leave them null. |
| `consecutive_days` | int | no | For checks with a suppression window. |
| `last_contacted` | date | no | Drives the queue's "already contacted" KPI. |
| `quality_score` | int | no | For `SCORE` columns. |
| `trend` | array of numbers | no | The row sparkline. |
| `detail` | object | no | Monitor-specific. The only untyped field — see step 4. |

Paging: `_fetch_incidents` follows `meta.total` at `page_size=500` up to 20 pages and
returns one merged snapshot. Filtering, searching and sorting then happen locally over that
snapshot, so the controls respond without a refetch.

Status vocabulary the app already colours and labels:

| Tone | Tokens |
|---|---|
| red | `contact_due`, `not_contacted`, `overdue` |
| amber | `awaiting_reply`, `contacted`, `monitoring` |
| green | `resolved`, `on_target` |
| grey | `new` |

An unrecognised token still renders — de-slugged, in grey — so a new status will not break
a page. If it is a status the fleet will use routinely, add it to `monitors/thresholds.py`
with a test.

### Trend

```json
{"data": [{"date": "2026-07-23", "open_count": 9, "past_threshold_count": 4}], "meta": {...}}
```

Roughly 30 points, one per snapshot. Out-of-order points are sorted before charting. An
empty array renders "No trend history", not an error.

### Summary — this is what puts the card on the home page

`/summary` carries one entry per monitor in `data.monitors`:

```json
{"monitor_id": "zero_future", "count": 14, "past_threshold_count": 3,
 "sparkline": [9, 10, 12, 12, 13, 14, 14]}
```

The home-page card and the sidebar badge come from this entry matched to the registry by
`monitor_id`. A registered monitor the API does not report yet shows as a zero/green card
rather than disappearing — that is deliberate, so a registry entry landing before its
endpoint is visible instead of silently missing.

### Contact queue

`/contact-queue` returns the union of every monitor's incidents at or past the threshold, in
the same incident shape. Rows whose `monitor_id` this build does not register are dropped
from the table and named in a caption, so the app never renders a row it cannot explain.

### Failure handling

Already implemented; do not add try/except in a page. `api/client.py` maps outcomes onto
typed errors and `components/errors.py` renders each one:

| Outcome | Error |
|---|---|
| timeout, connection failure, `5xx` | `ApiUnavailable` |
| `401`, `403` | `ApiUnauthorized` |
| `404` | `ApiNotFound` |
| other `4xx`, non-JSON body, shape mismatch | `ApiContractError` |

Reads are cached with `@st.cache_data(ttl=3600)` at the repository layer only — the batch
refreshes once a day, so there is no refresh button.

## 3. Registry entry

Add the `Monitor` and append it to `MONITOR_REGISTRY` in
`src/stewards/monitors/registry.py`. Registry order is the order of the sidebar and of the
home-page cards.

```python
ZERO_FUTURE = Monitor(
    id="zero_future",
    name="Zero future opportunities",
    group=Group.CONTENT,
    severity=Severity.HIGH,
    blurb=(
        "Feeds that parse cleanly but contain no opportunity starting after the snapshot "
        "date. These pass every availability check while being invisible to consumers, so "
        "they are reported separately from stalls and endpoint failures."
    ),
    unit="feeds at zero",
    detail_model=ZeroFutureDetail,
    columns=(
        Col("publisher_name", "Publisher", ColKind.TEXT, primary=True),
        Col("feed_name", "Feed", ColKind.MONO),
        Col("detail.future_count", "Future items", ColKind.NUMBER),
        Col("detail.last_nonzero", "Last non-zero", ColKind.DATE),
        Col("days_open", "Days at zero", ColKind.DAYS),
        Col("trend", "30d trend", ColKind.SPARKLINE),
        Col("status", "Status", ColKind.STATUS),
        Col("feed_url", "Endpoint", ColKind.LINK, help="Opens the publisher's feed endpoint"),
    ),
    filters=(
        FilterSpec("feed_type", "Feed type"),
        FilterSpec("status", "Status"),
    ),
    query="monitor_zero_future_v1",
    page="views/20_zero_future.py",
    kpi_labels=("feeds at zero", "publishers affected", "past threshold"),
)

MONITOR_REGISTRY: tuple[Monitor, ...] = (
    SINGLE_FEED_STALL,
    HTTP_FAILURE,
    ZERO_FUTURE,
)
```

A `Col.field` is either an attribute of `Incident` or `detail.<name>`. Nothing else. There is
nothing to register in `app.py`: `components/nav.py` builds the sidebar by iterating
`MONITOR_REGISTRY` and grouping on `Monitor.group`.

## 4. Detail model, if the monitor has detail fields

`Incident.detail` is the one untyped field in the contract, and it is only ever read through
the detail model its monitor declares — never with a string key in a page. Add a subclass in
`src/stewards/api/models.py`:

```python
class ZeroFutureDetail(DetailModel):
    future_count: int | None = None
    last_nonzero: date | None = None
```

Every field optional with a default. Unknown keys are ignored and missing keys default, so a
detail field the API has not started sending yet renders as an em dash instead of raising.
This is the only edit to a shared module that adding a monitor may make, and it is purely
additive.

## 5. Page stub

`src/stewards/views/20_zero_future.py`, three lines, no logic:

```python
from stewards.components.monitor_page import render_monitor_page
from stewards.monitors.registry import get_monitor

render_monitor_page(get_monitor("zero_future"))
```

Conventions that matter:

- The file lives in `views/`, **never** in a folder named `pages/`. A `pages/` folder beside
  `app.py` switches Streamlit into v1 multipage mode, where every page file becomes its own
  entrypoint — a deep link would then run the page script directly and bypass the auth gate.
  `tests/smoke/test_pages.py` guards this.
- The numeric prefix is stripped from the route: `views/20_zero_future.py` serves
  `/zero_future`.
- Prefix ranges follow the sidebar groups: `00–09` cross-cutting (overview, contact queue),
  `10s` availability, `20s` content, `30s` coverage and quality. Leave gaps.

## 6. The email draft, if the default sentence is not good enough

Selecting a table row opens a copyable publisher email — the app's only output. It works for
a new monitor with no changes: `monitors/email_draft.py` falls back to a generic
observation ("this feed has been failing the <monitor name> check for N days. First detected
<date>.").

For publisher-facing copy specific to the check, add an entry to `_OBSERVATIONS` keyed by
monitor id, and, if the sentence should cite a detail date rather than `first_detected`, an
`_EVIDENCE_FIELDS` entry naming the detail attribute. Both are per-monitor lookups with a
default, so this is additive. The drafted message is a golden-file test — update
`tests/unit/test_email_draft.py` alongside it.

## 7. The home-page card and the sidebar badge

Both are automatic. No page code, no layout work:

- `monitors/overview.build_tiles` emits one card per registry entry, in registry order, from
  the matching `/summary` monitor counts.
- `monitors/overview.nav_badges` emits the sidebar count pill. A monitor with nothing open
  gets no pill, so the sidebar shows only what needs attention.
- Card state and tone: green with nothing open; red once anything is past the threshold;
  grey when non-zero and `INFORMATIONAL`; amber otherwise. The sidebar pill reuses the same
  tone, so the two can never disagree.
- The card's sparkline is `MonitorCount.sparkline`; fewer than two points draws nothing at
  all rather than an empty axis.

The only thing needed to make the card show real figures is the `/summary` entry for the new
`monitor_id` — from the live API, or from `summary.json` in sample-data mode (next step).

## 8. Sample data

The app ships bundled payloads so it runs and can be reviewed before the API exists:

```bash
STEWARDS_USE_SAMPLE_DATA=true STEWARDS_ENV=dev STEWARDS_DISABLE_AUTH=true \
  uv run streamlit run src/stewards/app.py
```

`api/sample_transport.py` serves `src/stewards/api/sample_data/` through an
`httpx.MockTransport`, resolving paths by file stem:

| Request path | File |
|---|---|
| `…/summary` | `summary.json` |
| `…/contact-queue` | `contact_queue.json` |
| `…/monitors/<id>/incidents` | `<id>_incidents.json` |
| `…/monitors/<id>/trend` | `<id>_trend.json` |

A path with no matching file returns 404, which surfaces as `ApiNotFound` on the page. So a
new monitor needs **two new files, and two edits to existing ones**:

1. **`<id>_incidents.json`** — full envelope. Every incident's `monitor_id` must be the new
   id. Cover the cases the tests are required to exercise:
   - one incident past the threshold,
   - one below it,
   - one at exactly `threshold_days` (the boundary that is most likely to be quietly wrong),
   - one with a null optional field and a null/absent `detail` key.
2. **`<id>_trend.json`** — around 30 points ending on the snapshot date.
3. **`summary.json`** — append a `MonitorCount` for the new id to `data.monitors`, with a
   `sparkline` of at least two points. Without this the card renders at zero and the
   overview smoke test that counts one sparkline per monitor fails.
4. **`contact_queue.json`** — optional, but add a row or two for the new monitor if you want
   it represented in the cross-monitor queue.

Keep `meta.snapshot_date` at `2026-08-21` across every sample payload: the smoke tests assert
that date, and mixed snapshot dates would make the app contradict itself on screen.

These payloads are also the happy-path contract fixtures for the tests — one copy of each
shape. Test-only variants (empty, malformed, paginated) belong in `tests/fixtures/`.

## 9. Switching to the real API

Nothing in the app changes. Point `STEWARDS_API_BASE_URL` at the API, set
`STEWARDS_API_TOKEN`, and drop `STEWARDS_USE_SAMPLE_DATA`. Any page in sample-data mode
renders a banner saying so, so there is no ambiguity about which mode is on screen.

| Variable | Meaning |
|---|---|
| `STEWARDS_API_BASE_URL` | Required unless sample-data mode is on |
| `STEWARDS_API_TOKEN` | Bearer token for the API; not the user's identity |
| `STEWARDS_USE_SAMPLE_DATA` | Serve the bundled payloads instead of calling the API |
| `STEWARDS_CONTACT_THRESHOLD_DAYS` | Fleet contact threshold, default 7 |

Secrets come from the environment or `.streamlit/secrets.toml` only. Never commit a token,
never log one.

A monitor whose data does not fit `/monitors/{id}/incidents` needs a new repository function,
and that is a change to shared code: add the endpoint to `api/repository.py` (a cache-free
`_fetch_*` plus a cached wrapper), a model in `api/models.py`, and a `respx`-backed test in
`tests/contract/`. Do not call `httpx` from anywhere but `api/client.py`.

## 10. Tests

### What you get for free

`tests/unit/test_registry.py` is parametrised over the whole registry, so a new entry is
validated the moment it lands: unique id, page module exists, blurb and unit present,
positive threshold, unique column labels, primary first column, both sample payloads exist,
every `Col.field` and `FilterSpec.field` resolves against the payload, the detail model
validates every payload `detail`, and the payload's `monitor_id` matches the registry.

`tests/smoke/test_pages.py` also asserts, over the whole registry, that every monitor page
lives under `views/` and that the overview renders a card and a sparkline per monitor.

### What you must add

A `tests/unit/test_<id>.py` module. Every pure function gets a happy path, an empty-input
case and one boundary case:

- `to_dataframe(monitor, incidents)` produces the declared columns, in the declared order;
- `days_open == threshold_days` classifies as past threshold;
- an empty payload yields an empty frame with the declared columns, not an exception;
- `monitor_kpis` counts incidents, distinct publishers and past-threshold rows;
- the monitor appears in the home-page cards and, if it has queue rows, in the queue union.

### Existing tests you must update

Adding a monitor changes fleet-wide totals, so these hard-coded expectations need editing —
they are the ones that will fail:

| File | What to update |
|---|---|
| `tests/smoke/test_pages.py` | add the new view filename to `PAGES` and `MONITOR_PAGES` |
| `tests/unit/test_overview.py` | `test_sidebar_counts_maps_every_reported_monitor` asserts the exact count dict from `summary.json` |
| `tests/smoke/test_pages.py` | `test_contact_queue_lists_the_cross_monitor_union` asserts the queue row count and the exact set of monitor names |

### The bar

`uv run pytest` must pass. Coverage on `src/stewards/{monitors,components,api}` ≥ 90%,
project ≥ 80%. Add a regression test with the offending payload whenever you fix a data bug.

## 11. Runbook

A dashboard reports what is broken; a runbook says what to do about it. Add
`docs/<name>-runbook.md` covering detection, triage sequence, what to tell the publisher and
when to escalate, and link it from `docs/index.md`. Runbooks are not in the app: they live
here and are published to GitHub Pages, and the sidebar's Documentation row links out to
this site.

## Checklist

- [ ] registry entry appended to `MONITOR_REGISTRY`, page module path correct
- [ ] detail model added to `api/models.py` if the monitor has detail fields
- [ ] three-line page stub in `views/`, numeric prefix matching its group range
- [ ] `<id>_incidents.json` and `<id>_trend.json` in `api/sample_data/`, with a past-threshold
      row, a below-threshold row, an exactly-at-threshold row and a null optional field
- [ ] `summary.json` carries a `MonitorCount` for the new id, sparkline included
- [ ] home-page card shows the right count, state colour and unit noun
- [ ] sidebar badge shows the open count
- [ ] page renders: header with snapshot date, blurb, three KPIs, trend, filters, table
- [ ] selecting a row opens an email draft that names the monitor and the days open
- [ ] `tests/unit/test_<id>.py` added; the fleet-total tests above updated
- [ ] `uv run pytest -q --cov=src/stewards --cov-report=term-missing` green and at the bar
- [ ] `uv run ruff check --fix . && uv run ruff format . && uv run mypy src` clean
- [ ] runbook page added and linked from `docs/index.md`

## Constraints you may not work around

1. **Pages contain no logic.** A page module calls one `render_*_page` component and nothing
   else. All shaping, filtering, derivation and formatting lives in pure functions under
   `monitors/`.
2. **No Streamlit imports in testable logic.** Anything that computes a value must be
   importable without a Streamlit runtime. If a function needs `st`, it belongs in a
   component and must be a thin renderer.
3. **No raw dicts past the client boundary.** `api/client.py` returns parsed JSON;
   `api/repository.py` returns pydantic models. Pages and components see models or
   DataFrames.
4. **Adding a monitor must not touch shared code** beyond an additive detail model. If it
   does, generalise the component.
5. **Read-only.** No mute, assign, re-crawl or send-email actions. A copyable email draft is
   the only output, and no download button goes back into the header.
6. **Every data page states its snapshot date**, via `components.layout.render_header`. A
   page that failed to load renders `render_error_header` instead — no snapshot line,
   because there is no snapshot.
7. **The page module lives in `views/`, never `pages/`.**
8. **Copy tone:** factual, no exclamation marks, no emoji in UI text, ISO dates everywhere
   they describe data.
