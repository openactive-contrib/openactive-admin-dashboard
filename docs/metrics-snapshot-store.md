---
title: Metrics snapshot store
---

# Metrics snapshot store

The framework's 18 dashboard-sourced measures are read from endpoints that hold **current
state only**. `feed_quality`, `opportunities` and `active_opportunities_summary` are rebuilt
every night with no `as_of` and no history, so the API cannot say what last month's figure
was and never will.

Four measures are defined as a trend — KPI4 and KPI7 are "tracked monthly", KPI6 is an
"improvement of the %". Recording a snapshot ourselves is the only way those ever get one,
and a month nobody records is gone permanently. This store is that record.

The measures themselves are fixed in `docs/metric-definitions.md` in the
`open-active-monitor-api` repository; the build they belong to is `docs/metrics-report-plan.md`
beside it.

## Where it lives

```
src/stewards/report/snapshots/<period>.json
```

Inside the package, not at the repository root, for one practical reason: the container image
copies `src` and nothing else, so a store under `src` ships with the image and needs no
Dockerfile change. `src/stewards/assets/` already holds bundled data on the same basis, and
`geography.py` resolves it from `Path(__file__)`. The store module resolves its directory the
same way.

Git is the durability layer. A period file is committed, so the history is versioned,
reviewable in a pull request and diffable. No database, no bucket, no credentials.

## Periods

A period is one calendar month, named `YYYY-MM`, and the file is `2026-11.json`. A period is
captured on the first of the following month, after 07:00 UTC — every admin response is
cached until then.

**Monthly is the only grain.** Quarterly and six-monthly reporting are views over monthly
periods, not separate files, so there is one series to reason about rather than three.

A monthly record is a **month-end reading, not a monthly aggregate.** The sources hold
current state, so a capture describes the moment it ran. This matters for CM13, which is to be
reported as a month-end figure *and* a monthly average: the average cannot come from this
store and needs a daily series of its own. Out of scope here, recorded so it is not assumed.

## What is stored

**Raw API fields, never computed measures.** A measure's definition can still move — KPI7 is
unsettled between `labelled_percentage` and `linked_percentage`, KPI4's baseline is unpinned
and CM13's is being restated. A store holding derived values would orphan its own history
the first time one of those settled. Holding the fields means a definition agreed next year
can be re-run across every month already captured.

So both sides of an open question are stored. `labelled_percentage` and `linked_percentage`
both go in; whichever KPI7 turns out to be, the series is already there.

The subset is what the 18 measures need, at the grain their derivation needs — no more.
KPI4 is volume-weighted per feed, so each feed's completeness fields and
`num_future_opportunity_items` are kept. The ~5,000 activity-district rows and ~10,000
site-venue mappings are not: no measure derives from them. A period file should stay well
under 1 MB.

## The record

One JSON object per file. Two-space indent, keys sorted, trailing newline, so a diff is
readable.

```json
{
  "period": "2026-11",
  "captured_at": "2026-12-01T07:14:02Z",
  "readings": {
    "summary": {
      "endpoint": "/admin/summary",
      "snapshot_date": "2026-11-30",
      "fields": {
        "feeds": 446,
        "publishers_monitored": 172,
        "past_threshold_count": {
          "single_feed_stall": 63,
          "dataset_stall": 12,
          "feed_ingestion_error": 1
        }
      }
    },
    "activity_coverage_summary": {
      "endpoint": "/admin/activity-coverage/summary",
      "snapshot_date": "2026-11-30",
      "fields": {
        "activity_opportunities": 671727,
        "labelled_percentage": 77.5,
        "linked_percentage": 64.3,
        "activities_offered": 293,
        "vocabulary_size": 768,
        "min_similarity": 0.85
      }
    },
    "active_places_coverage": {
      "endpoint": "/admin/active-places-coverage",
      "error": "404 Not Found"
    }
  }
}
```

`captured_at` is when the job ran. `snapshot_date` is the reading's own
`meta.snapshot_date`, recorded per reading because they genuinely differ — Active Places has
answered two months behind the rest. The freshness guard reads these, so they are not
decoration.

`min_similarity` is stored with CM8 because `activities_offered` depends on it. A figure
whose meaning rests on a parameter stores the parameter.

## Rules

- **A `null` is never a zero.** A field the batch did not compute is stored as `null` and
  reported as "not available". This is the same rule the dashboard applies to `/summary`
  counts.
- **A failed reading is recorded, not omitted.** It carries `error` instead of `fields`, so
  the gap is visible and distinguishable from a genuine zero.
- **A partial capture is still written.** A month half-recorded beats a month lost. Refusing
  to *publish a report* on partial data is a separate decision, made by the report job.
- **A period file is written once.** Re-capturing needs an explicit `--force`, and the
  overwrite shows up in git history.

## Known gap

CM9 is registered against the analytics `/summary` → `number_of_facilities`. The app's client
builds admin paths only and has no entry for that surface, so the capture either gains an
endpoint entry or records CM9 as unavailable. Settled when the capture is built, not here.

## Reading it

`stewards.report.store` is the only reader and writer: append a period, read a period, read
the period before a given one. It touches the filesystem and nothing else — no API client, no
Streamlit — so it is unit-testable on a temporary directory.

The KPI page reads this store rather than the API, so the page and the report cannot disagree,
and the page can show change over time that no endpoint could supply.