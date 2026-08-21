# OpenActive Data Stewards Dashboard — Build Brief

Internal Streamlit app for the ODI tech team to monitor the health and quality of data
published by ~170 OpenActive publishers. Read-only. Data is a **daily batch snapshot** in
BigQuery — never described or charted as live.

Companion artifact: `Data Stewards Dashboard.dc.html` (UI mockup — layout, hierarchy,
copy tone, colour semantics). Match its structure, not its pixel-level CSS; use native
Streamlit widgets throughout.

---

## 1. Product decisions (already settled — do not re-litigate)

| Decision | Value |
|---|---|
| Landing screen | **Monitor overview** — one tile per monitor with affected-publisher count, state chip, sparkline |
| Navigation | **Grouped sidebar sections** (`st.navigation` with a dict of section → pages) |
| Primary drill-down | **Monitor page** — all publishers failing one check |
| Actions available | Read-only + **Export CSV** + **draft publisher email**. No mute, assign, or re-crawl. |
| Charting | Sparklines in tables, one 30-snapshot trend chart per monitor, histogram only for coverage spread |
| Docs | Searchable index, tag filters, full markdown page view, behind auth |
| Contact threshold | Incident open ≥ **7 days** (configurable) → enters the cross-monitor Contact queue |
| Quality score | 0–100 from an existing internal function: required-field completeness, recommended-field completeness, vocabulary conformance |

---

## 2. Auth

- Google OIDC via Streamlit's native `st.login()` / `st.user` (`.streamlit/secrets.toml` →
  `[auth.google]` with `client_id`, `client_secret`, `server_metadata_url`, `redirect_uri`).
- Hard allowlist on email domain `theodi.org`; reject everything else with a plain message,
  no partial UI.
- Session expiry 12 hours; `cookie_secret` from env, never committed.
- **Every** page, including all docs, is gated. Put the gate in `app.py` before
  `st.navigation(...).run()` so no page module can be reached unauthenticated.

```python
if not st.user.is_logged_in:
    render_login_screen()  # single centred card, "Continue with Google"
    st.stop()
if not st.user.email.endswith("@theodi.org"):
    st.error("Access is limited to the ODI Google workspace.")
    st.stop()
```

---

## 3. Data access — REST API

The app is a **client only**. All data comes from the stewards REST API, which fronts the
daily BigQuery batch. No BigQuery SDK, no SQL, no database credentials in this codebase.
See `.claude/skills/api-integration/SKILL.md` for the client/model/repository layering.

### Envelope

Every list endpoint returns:

```json
{ "data": [ ... ],
  "meta": { "snapshot_date": "2026-08-21", "generated_at": "2026-08-21T06:12:04Z",
            "total": 23, "page": 1, "page_size": 100 } }
```

`meta.snapshot_date` is rendered in every page header. Auth is
`Authorization: Bearer <STEWARDS_API_TOKEN>`; the user's Google identity gates the app, it is
not the API credential.

### Endpoints

```
GET /api/v1/summary                       overview KPIs + per-monitor tile counts + sparklines
GET /api/v1/monitors                      monitor registry as served by the API
GET /api/v1/monitors/{id}/incidents       ?past_threshold=&search=&page=&page_size=
GET /api/v1/monitors/{id}/trend?days=30   { date, open_count, past_threshold_count }[]
GET /api/v1/contact-queue                 cross-monitor union, days_open >= threshold
GET /api/v1/quality?expand=feeds          publisher rows with nested feed rows
GET /api/v1/coverage/active-places        rows + match-rate histogram buckets
GET /api/v1/feeds/{feed_id}/schema-diff   { change, field_path, from_type, to_type, impact }[]
GET /api/v1/publishers/{id}               publisher metadata + contact history
```

### Incident shape (returned by the API, not computed here)

```json
{ "monitor_id": "single_feed_stall", "publisher_id": "...", "publisher_name": "Freedom Leisure",
  "feed_id": "...", "feed_name": "scheduled-sessions", "feed_type": "ScheduledSession",
  "feed_url": "https://...", "first_detected": "2026-07-30", "days_open": 22,
  "consecutive_days": 22, "past_threshold": true, "status": "contact_due",
  "last_contacted": null, "trend": [4,4,4,4,4,4,4], "detail": { }, "quality_score": 82 }
```

`detail` is a monitor-specific object (http_status/error_class, orphan_count/orphan_pct,
future_count/last_nonzero, matched/unmatched, etc.). Model it per monitor in
`api/models.py`; never index into it with string keys in a page.

**The API owns `days_open` and `past_threshold`.** The app must not recompute them from
dates — but it must test its own display logic against the boundary (`days_open == threshold`
is past threshold).

### Backing schema (for reference — the API team's side, not ours)

```
publishers        publisher_id, name, region, active_places_org_id, contact_email
datasets          dataset_id, publisher_id, dataset_url, discovered_at
feeds             feed_id, dataset_id, feed_url, feed_type, item_kind      -- ScheduledSession | SessionSeries | Slot | FacilityUse
feed_snapshots    snapshot_date, feed_id, http_status, error_class, error_detail,
                  fetch_ms, item_count, future_item_count, max_modified,
                  orphan_child_count, schema_fingerprint, quality_score,
                  required_pct, recommended_pct, vocab_pct
schema_fields     snapshot_date, feed_id, field_path, field_type          -- for drift diffs
active_places     publisher_id, snapshot_date, facilities_in_feed, facilities_matched
```

Partitioned on `snapshot_date`, clustered on `feed_id`.

Incidents are **derived, not stored**: for each key, the current unbroken run of snapshot
dates where the monitor condition holds, ending at the latest snapshot. `first_detected` is
the run start, `days_open` is measured from it (a missing daily run must not reset the age),
and runs shorter than 2 snapshots are suppressed to kill single-day blips.

`publisher_contacts(publisher_id, monitor_id, contacted_on, contacted_by, note)` supplies
"last contacted". If the API exposes no write endpoint for it in v1, show `—`.

---

## 4. Monitor registry — the extension point

Adding a monitor must be **one SQL file + one registry entry + one 5-line page stub**.

```python
# monitors/registry.py
Monitor = dataclass(
    id,
    name,
    group,  # group: "Availability" | "Content" | "Coverage & quality"
    severity,  # "critical" | "high" | "medium" | "informational"
    blurb,  # 2-3 sentences shown at the top of the page
    sql,  # path under data/queries/
    key_cols,  # e.g. ("publisher_id","feed_id")
    columns,  # ordered ColumnSpec list -> st.column_config
    threshold_days=7,
    extras=(),  # e.g. ("schema_diff",) | ("coverage_histogram",)
)
```

The overview page renders tiles by iterating the registry — no per-monitor code there.

### The eight v1 monitors

| id | Name | Condition (plain English) | Notable columns |
|---|---|---|---|
| `single_feed_stall` | Single-feed stalls | `max_modified` unchanged vs previous snapshot **and** `http_status = 200`; exclude feeds whose whole dataset is stalled | publisher, feed, type, last modified, days stalled, trend, status |
| `dataset_stall` | Dataset-wide stalls | **all** feeds on a dataset stalled the same day; grouped at dataset level | publisher, dataset, feeds affected, last modified any feed, days stalled |
| `http_failure` | Continuous HTTP endpoint failures | `http_status != 200` or timeout/TLS error, ≥2 consecutive days | publisher, feed, HTTP, error, consecutive failures, last success |
| `zero_future` | Zero future opportunities | `future_item_count = 0` while fetch succeeds | publisher, feed, future count, 7d ago, last non-zero day, days at zero |
| `orphan_children` | Datasets with orphaned child opportunities | child items whose parent id is absent from the parent feed; alert when ratio > 5% | publisher, child feed, orphans, % of feed, distinct missing parents |
| `schema_drift` | Data schema drift | `schema_fingerprint` changed; diff `schema_fields` between snapshots | publisher, feed, change (added/removed/type change), field, type, impact |
| `active_places_coverage` | Active Places coverage | `facilities_matched / facilities_in_feed`, target ≥ 80% | publisher, facilities, matched, match %, unmatched, trend |
| `quality_table` | Publisher quality table | not an incident monitor — full 170-publisher table, expandable to per-feed rows | score 0–100, 7d Δ, required %, recommended %, vocabulary %, open incidents |

Cross-monitor view: **Contact queue** — union of all monitors where `days_open >= 7`,
oldest first, with monitor name, detail, first detected, last contacted.

---

## 5. Page map

```
app.py                       auth gate + st.navigation
  Overview
    00_overview.py           4 KPI metrics; amber threshold banner -> contact queue; monitor tiles
    01_contact_queue.py      cross-monitor, days_open >= threshold
  Availability
    10_single_feed_stalls.py
    11_dataset_stalls.py
    12_http_failures.py
  Content
    20_zero_future.py
    21_orphaned_children.py
    22_schema_drift.py       + schema diff panel for the selected row
  Coverage & quality
    30_active_places.py      + match-rate histogram across 170 publishers
    40_quality_table.py      publisher rows, expandable per-feed
  Knowledge base
    90_docs.py               search + tag filter + markdown view
```

Sidebar items carry a count badge where the monitor has open incidents (use the label,
e.g. `"Single-feed stalls (23)"` — Streamlit nav labels are plain text).

### Standard monitor page composition

```python
render_header(monitor)  # blurb + mono meta chips (monitor id, severity, threshold, schedule)
render_kpis(monitor, df)  # 3 st.metric in st.columns
render_trend(monitor, hist_df)  # 30-snapshot line: total open + dashed line for past-threshold
render_filters(monitor)  # text_input search, selectboxes, "Past threshold only" toggle
render_table(monitor, df)  # st.dataframe, hide_index, use_container_width
render_footer(monitor)  # "Read-only view" + query name, Export CSV via st.download_button
```

---

## 6. Table rendering (Streamlit specifics)

Use `st.dataframe` with `column_config` — no custom components needed:

- days open / status → `TextColumn` rendered from a precomputed label; colour semantics via
  emoji-free text plus a `pandas.Styler` background on the severity column
  (`red #FBEDEC/#C6413B`, `amber #FDF3E3/#C77F1A`, `green #EAF4EE/#1F7A4C`, `grey #F1F4F5/#5C6B76`)
- trend → `LineChartColumn(y_min=0)` from a list-of-floats column
- match % / quality score → `ProgressColumn(min_value=0, max_value=100, format="%d")`
- feed URL → `LinkColumn(display_text="feed ↗")`
- counts and dates → `NumberColumn(format="%,d")` / ISO date strings in a monospace-ish column

Row selection (`on_select="rerun"`, `selection_mode="single-row"`) drives:
- the **schema diff panel** on the drift page (`st.code` of the field-level diff, added lines
  green, removed red — build the diff string server-side)
- the **draft email** action: `st.popover("✉ Draft email")` containing a `st.code` block with
  a prefilled, copyable message (publisher name, feed, days open, what we observed, what we
  need) — copy only, no sending.

Export CSV = `st.download_button` over the currently filtered dataframe.

---

## 7. Visual language

Streamlit's own theme, tuned in `.streamlit/config.toml`:

```toml
[theme]
base = "light"
primaryColor        = "#0E8F8A"   # teal, actions and links
backgroundColor     = "#FFFFFF"
secondaryBackgroundColor = "#F1F4F5"
textColor           = "#16202A"
font                = "sans serif"   # Source Sans 3
```

Semantic colours (use consistently, nowhere else): red `#C6413B` critical / past threshold ·
amber `#C77F1A` warning / monitoring · green `#1F7A4C` healthy / on target ·
grey `#5C6B76` new / informational. Monospace for ids, dates, counts and feed names.

Copy tone: factual, no exclamation, no emoji. State the snapshot timestamp in the header of
every page (`Snapshot 2026-08-21 06:00 · BigQuery daily batch`) so nothing reads as live.

---

## 8. Documentation section

- Docs are markdown files in `docs/` with front matter:
  `title, tags[], owner, updated, sensitivity: internal|restricted`.
- Index: search box (title + body + tags, simple case-insensitive substring is fine),
  tag chips as a multiselect, result cards showing title, excerpt, tags, updated, owner.
- Page view: rendered markdown, tag row, an "internal only" tag rendered in red, breadcrumb
  back to the index, and an on-this-page heading list from the markdown h2s.
- Seed with: single-feed stalls runbook, publisher contact policy, quality score definition,
  BigQuery snapshot model, known false positives register, Active Places reconciliation notes.

---

## 9. Performance & operational notes

- `@st.cache_data(ttl=3600)` at the repository layer; data changes once a day, so cache
  generously and show the snapshot caption rather than a refresh button.
- Request `page_size=500` and page inside the repository function; pages never loop.
- Distinct error states per failure type: API unavailable (5xx/timeout), unauthorized,
  contract mismatch. Never a silent empty table.
- The API token lives in secrets and is never logged. The user's Google identity is for
  access control only.
- Log page views with user email to an audit endpoint if wanted (confirm first).

## 9b. Code quality bar

Python 3.12 · Streamlit · httpx · pydantic v2 · pandas · uv · ruff · mypy --strict · pytest.
Pages are thin (fetch → component, no logic); all computation lives in Streamlit-free pure
functions. Coverage ≥ 90% on `monitors`, `components`, `api`; ≥ 80% overall. Full rules in
`CLAUDE.md` and `.claude/skills/testing/SKILL.md`.

---

## 10. Out of scope for v1

Publisher pages, feed detail pages, alerting/email sending, mute or assign workflows,
re-crawl triggers, public sharing of any view, mobile layout.
