---
name: add-monitor
description: Add a new monitor dashboard to the stewards app. Use whenever the user asks for a new health/quality check, a new dashboard page, or a new incident type.
---

# Adding a monitor

A monitor is a named check that produces **incidents** (a publisher/feed failing the check,
with an age in days). Adding one must touch exactly four things. If it touches more, the
shared code is not general enough — fix that instead of special-casing.

## 1. Registry entry — `src/stewards/monitors/registry.py`

```python
Monitor(
    id="zero_future",
    name="Zero future opportunities",
    group=Group.CONTENT,  # OVERVIEW | AVAILABILITY | CONTENT | COVERAGE
    severity=Severity.HIGH,  # CRITICAL | HIGH | MEDIUM | INFORMATIONAL
    blurb="The feed parses cleanly but contains no opportunity starting after the "
    "snapshot date. Invisible to consumers even though availability checks pass.",
    # The id is the API path segment: GET /api/v1/monitors/zero_future/incidents
    unit="feeds at zero",  # the noun under the count, on the tile and the first KPI
    key_cols=("publisher_id", "feed_id"),
    columns=[
        Col("publisher_name", "Publisher", kind=ColKind.TEXT, primary=True),
        Col("feed_name", "Feed", kind=ColKind.MONO),
        Col("future_count", "Future count", kind=ColKind.NUMBER),
        Col("last_nonzero", "Last non-zero day", kind=ColKind.DATE),
        Col("days_open", "Days at zero", kind=ColKind.DAYS),  # auto RAG vs threshold
        Col("trend", "30d trend", kind=ColKind.SPARKLINE),
        Col("status", "Status", kind=ColKind.STATUS),
    ],
    threshold_days=7,
    has_threshold_filter=True,  # False for informational monitors
    extras=(),  # "schema_diff" | "coverage_histogram"
)
```

`ColKind` drives the `st.column_config` mapping — never write column_config in a page.

## 2. Page stub — `src/stewards/views/20_zero_future.py`

Three lines. No logic:

```python
from stewards.components.monitor_page import render_monitor_page
from stewards.monitors.registry import get_monitor

render_monitor_page(get_monitor("zero_future"))
```

Nothing to register in `app.py`: `components/nav.py` builds the sidebar by iterating
`MONITOR_REGISTRY` and grouping on `Monitor.group`.

Register it in the `st.navigation` section dict in `app.py` under its group.

## 3. Payloads — `src/stewards/api/sample_data/`

`zero_future_incidents.json` and `zero_future_trend.json`, in the envelope shape. Include
one row past threshold, one below, one at exactly `threshold_days`, and one with a null
optional field. These are both the sample-data payloads the app serves before the API exists
and the happy-path fixtures the tests assert on. Test-only variants (empty, malformed,
paginated) go in `tests/fixtures/`.

## 4. Tests — `tests/unit/test_zero_future.py`

- `tests/unit/test_registry.py` already covers the registry entry for free (unique id,
  page module exists, sample payloads exist, every `Col.field` and `FilterSpec.field`
  resolves, detail model validates) — just add the payloads and it runs
- `to_dataframe(fixture)` produces the declared columns in declared order
- `days_open == threshold` is classified past-threshold (boundary)
- empty payload renders an empty table, not an exception
- the monitor appears in the overview tile list and in the contact queue union

## Checklist before declaring done

- [ ] `uv run pytest -q` green, new code ≥ 90% covered
- [ ] monitor tile appears on the overview with correct count and state colour
- [ ] sidebar label carries the open-incident count
- [ ] Export CSV exports the *filtered* frame
- [ ] draft-email popover names the monitor and the days open
- [ ] a runbook markdown page exists in `src/stewards/docs/` tagged `runbook`
- [ ] `uv run ruff check . && uv run mypy src` clean
