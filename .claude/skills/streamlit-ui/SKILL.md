---
name: streamlit-ui
description: How to build pages and components in this app so they match the approved mockup. Use when creating or editing any page, table, chart, or control.
---

# Streamlit UI conventions

Reference mockup: `Data Stewards Dashboard.dc.html`. Match structure and hierarchy, not CSS.
Everything below is achievable with native widgets — do not write custom components.

## Page composition (every monitor page, in this order)

1. **Header** — breadcrumb (group name), page title, right-aligned snapshot caption
   (`Snapshot 2026-08-21 06:00 · daily batch`) and an Export CSV `st.download_button`.
2. **Blurb card** — 2–3 sentences from the registry, plus monospace meta chips
   (monitor id, severity, contact threshold, schedule) via `st.caption`/`st.code` inline.
3. **KPIs** — exactly three `st.metric` in `st.columns(3)`, right-aligned block.
4. **Trend** — one 30-snapshot chart: solid line = open incidents, dashed = past threshold.
   Built by `monitors.trend.trend_chart` (Altair — `st.line_chart` cannot dash a series or
   drop its grey plot panel); y starts at 0; x labels are snapshot dates.
5. **Filters** — a `st.text_input` search, 2–3 `st.selectbox`, and a
   `st.toggle("Past threshold only")` **only when `monitor.has_threshold_filter`**
   (informational monitors like schema drift and Active Places coverage do not get it, and
   it defaults to off everywhere).
6. **Table** — `st.dataframe(..., hide_index=True, use_container_width=True,
   on_select="rerun", selection_mode="single-row")`.
7. **Footer** — "Read-only view. Actions: export, draft publisher email." + query/version id.

## Column config by `ColKind`

| ColKind | Widget |
|---|---|
| TEXT / MONO | `TextColumn` (MONO also gets a monospace-ish `help`) |
| NUMBER | `NumberColumn(format="%,d")` |
| DATE | `TextColumn` with ISO strings (not `DateColumn` — no timezone confusion) |
| DAYS | `TextColumn` showing `"12d"`, RAG-styled via the Styler |
| PERCENT / SCORE | `ProgressColumn(min_value=0, max_value=100, format="%d")` |
| SPARKLINE | `LineChartColumn(y_min=0, width="small")` (in tables; tiles use
             `monitors.trend.sparkline_chart`) |
| STATUS | `TextColumn`, RAG-styled |
| LINK | `LinkColumn(display_text="feed ↗")` |

RAG colouring uses a `pandas.Styler` background over the DAYS/STATUS/PERCENT columns only:
red `#FBEDEC`/`#C6413B`, amber `#FDF3E3`/`#C77F1A`, green `#EAF4EE`/`#1F7A4C`,
grey `#F1F4F5`/`#5C6B76`. Never colour a whole row.

## Row selection drives the detail affordances

- Selected row + `monitor.extras` contains `"schema_diff"` → render `st.code` of the
  field-level diff below the table, added lines prefixed `+`, removed `-`.
- Selected row → `st.popover("✉ Draft email")` with a `st.code` block containing a prefilled,
  copyable message: publisher, feed, what we observed, days open, what we need, by when.
  Copy only — the app never sends.

## Overview page

Four KPI cards (publishers monitored, publishers with issues, open incidents, past
threshold), then an amber banner when the contact queue is non-empty linking to it, then a
grid of monitor tiles built by iterating `MONITOR_REGISTRY` — `st.columns(3)` of
`surface.card(...)`, each laid out as: name + `st.badge` state chip on one row, group
caption, then the count beside its sparkline, then a rule and a note beside an
`st.page_link` to the monitor.

## Theme

The palette lives in `components/theme.py`; `.streamlit/config.toml` mirrors it onto
Streamlit's tokens (main area, `[theme.sidebar]` for the dark sidebar, and the semantic
colour slots behind `:red[…]`, `st.badge` and the alert boxes). Never inline a hex outside
`theme.py` — a test enforces it. The page background is the canvas tint and cards are white
via `components/surface.py`, the app's only stylesheet; wrap a card with `card("name")`.

## Copy

Factual, lowercase units, ISO dates, no emoji, no exclamation marks. Say what the number is
and when it was measured. Never imply live data.
