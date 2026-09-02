---
name: add-monitor
description: Add a new monitor dashboard to the stewards app. Use whenever the user asks for a new health/quality check, a new dashboard page, or a new incident type.
---
---
title: Adding a dashboard
description: How to add a new monitor dashboard to the app. Use whenever the user asks for a new health/quality check, a new dashboard page, or a new incident type.
---

# Adding a monitor

**`docs/adding-a-dashboard.md` is the procedure. Read it in full before editing anything.**
It carries what this file deliberately does not duplicate: the API contract field by field,
the sample-payload rules, the home-page card mechanics, the required test cases, and the
list of existing tests whose fleet-wide totals a new monitor breaks.

This file exists so the procedure is found; that file exists so it is correct in one place.
Do not restate it here — if the procedure changes, change the doc.

## What you are adding

A monitor is a named check producing **incidents** (one publisher/feed failing it, with an
age in days and a status). Shared components render the whole page from one registry entry,
so adding a dashboard is a declaration plus data, not a page build.

| File | Change |
|---|---|
| `src/stewards/monitors/registry.py` | one `Monitor(...)`, appended to `MONITOR_REGISTRY` |
| `src/stewards/api/models.py` | a `DetailModel` subclass — only if the monitor has `detail` fields |
| `src/stewards/views/NN_<name>.py` | three-line page stub, no logic |
| `src/stewards/api/sample_data/<id>_incidents.json` | happy-path payload, also the test fixture |
| `src/stewards/api/sample_data/<id>_trend.json` | ~30 snapshot points |
| `src/stewards/api/sample_data/summary.json` | append a `MonitorCount` for the new id (this is what makes the home-page card and sidebar badge real) |
| `tests/unit/test_<id>.py` | happy path, empty input, `days_open == threshold_days` boundary |

Nothing to register in `app.py`, and no `column_config`, tile or sidebar code to write: the
sidebar iterates `MONITOR_REGISTRY`, the cards come from the registry crossed with
`/summary`, and `ColKind` drives the table widgets.

## Non-negotiables

- Adding a monitor must not touch shared code beyond the additive detail model. If it does,
  generalise the component instead of special-casing the monitor.
- Pages contain no logic; anything that computes a value stays importable without Streamlit.
- The page module goes in `views/`, never `pages/` — a `pages/` folder beside `app.py`
  bypasses the auth gate.
- Read-only: no mute, assign, re-crawl or send actions, and no download button.
- Copy is factual, no exclamation marks, no emoji, ISO dates.

## Done means

`uv run pytest -q` green at the coverage bar, `uv run ruff check . && uv run mypy src`
clean, the card and sidebar badge correct on the overview, and a runbook page added to
`docs/` and linked from `docs/index.md`. The full checklist is at the end of
`docs/adding-a-dashboard.md`.
