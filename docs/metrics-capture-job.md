---
title: Metric capture job
---

# Metric capture job

On the 1st of each month a scheduled job reads the day's figures and records them against
the month just ended, in the metric snapshot store on the monitoring API. The KPI framework
measures are computed from that series when a report is produced.

The job is `python -m stewards.metrics`, run by `.github/workflows/capture-metrics.yml`. The
measurements it builds are `src/stewards/metrics/measurements.py`.

## Why it exists

The endpoints behind these figures report **current state only**. `feed_quality`,
`opportunities` and `active_opportunities_summary` are rebuilt every night with no `as_of`
and no history, so the API cannot say what last month's figure was and never will.

Several measures are defined as a trend — tracked monthly, or as an improvement on a
previous figure. Recording a snapshot ourselves is the only way those ever get one, and
**a month nobody captures cannot be recovered afterwards.**

The store itself is not in this repository. It is a BigQuery table behind
`/admin/metric-snapshots` on the monitoring API, which owns the table and the endpoint; this
repository owns the job that fills them, beside the other two scheduled jobs. The API's
`docs/admin-api.md` is the contract.

## What a row is

One row per `(period, source, field)`:

| Field | Meaning |
|---|---|
| `period` | The month the figure describes, as its first day, e.g. `2026-11-01` |
| `source` | Which read it came from: `feed_quality`, `activity_coverage`, `summary`, `analytics_summary`, `ecosystem_growth` |
| `field` | The field within that read |
| `value` | The figure. Counts and rates share the column |
| `denominator` | What `value` is out of, for a rate or a weighted mean. Null for a plain count |
| `snapshot_date` | The `meta.snapshot_date` of the response the figure was read from |
| `captured_at` | When the row was written. Set by BigQuery, never by the job |

A row is a **source field, not a finished measure.** Nothing in the store is called "KPI4";
a report computes that from the completeness rows when it runs. A measure whose definition
changes later can then be recomputed for every month already captured. Storing the finished
percentage would freeze today's definition into the history instead.

**A null is never a zero.** A figure the batch did not compute is stored as null and read
back as "not available".

## What it captures

| Source | Read | Fields |
|---|---|---|
| `feed_quality` | `/admin/feed-quality` | the fleet totals, and every completeness field twice |
| `activity_coverage` | `/admin/activity-coverage/summary` | the opportunity counts, and both the labelled and the linked share |
| `summary` | `/admin/summary` | the estate counts, and one row per monitor behind the critical-error measure |
| `analytics_summary` | `/summary` on the public surface | the national figures, including the facility count |
| `ecosystem_growth` | `/admin/ecosystem-growth-trend` | the month's `datasets_crawled` |

Three things in that table are decisions rather than transcription.

**Completeness is stored twice**, `_weighted` and `_unweighted`. The quality endpoint
averages its own summary unweighted — one feed, one vote — while the measurement asks for a
figure adjusted by volume of feed. Only the first is in the payload, so the second is
computed from each feed's `num_future_opportunity_items`. A feed with nothing in the future
carries no weight and drops out: it is not evidence about a fleet-wide rate, and counting it
as a zero would drag the figure down for nothing. The denominator records how many feeds
contributed, so a month computed over a handful of them can be seen for what it is.

**Both activity shares are stored.** Labelled and linked answer different questions — whether
an opportunity carries an activity label at all, and whether that label matches the
OpenActive activity list — and they move independently. Which one the measure means is not
settled, and keeping one would settle it by accident.

**The three monitor counts are stored one per monitor**, not summed, so the critical-error
measure can be redefined without losing the months already captured.

## The crawl figure is the odd one

`datasets_crawled` is an aggregate over the month rather than a reading taken on one day.
Two things follow.

It is only stored once the month has **ended**. An in-progress month's figure covers the
days so far and changes daily, and a captured measurement cannot be corrected.

It is also the only measure with any history behind it: the growth endpoint derives it from
the ingestion table, which reaches back to 2026-05-07. Those months can be recovered rather
than waited for, which is what the backfill below is for. Every other figure in the store
has to be captured as it happens.

## How a run goes

1. Work out the period — the month just ended, unless one was given.
2. Read the five endpoints. A read that fails costs its own measurements and nothing else.
3. Read what the store already holds for that period.
4. Send only what is missing, as one batch.

Step 3 is what makes the schedule safe. The job runs on the **1st to the 5th**, because
GitHub can delay or drop a scheduled run. A measurement may be captured only once and the
store refuses a batch in full if any of it is already there — so a job that re-sent the whole
month would be refused on day 2, and a month captured in part on day 1 could never be
completed. Sending only what is missing means day 2 fills the gap, and a month already
complete writes nothing and exits cleanly.

It runs at **08:00 UTC**, not UK time: every admin response is output-cached until 07:00 UTC,
so an earlier run would record the previous day's figures. The hour of margin should not move
with the clocks.

## When it fails

The run fails, and nothing is written, when the store itself cannot be read: without knowing
what is already there, a write would be refused in full.

The run fails but still captures what it can when a read fails. The next day's run fills the
gap. It also fails when the period is not the first of a month, or is a month that has not
ended — a mistyped period would be permanent, because there is **no update or delete route**.
A period captured wrongly has to be corrected in BigQuery.

A refusal naming every measurement sent is not a failure: it is what a second run looks like
once the month has landed.

Each run writes a summary to the workflow run page, listing every measurement captured and
every read that failed.

## Running it by hand

From the Actions tab, run **Capture monthly metrics**. **dry_run** is on by default, because
a capture cannot be undone: the run prints the measurements it would store and writes
nothing. **period** takes the first day of a month, and defaults to the month just ended.

```bash
ADMIN_API_BASE_URL=… ADMIN_API_TOKEN=… uv run python -m stewards.metrics --dry-run
```

The job needs no secrets beyond the `ADMIN_API_BASE_URL` and `ADMIN_API_TOKEN` the other two
jobs already use.

One thing to know if the deployment ever changes: the public `/summary` is gated on the API's
`Api:AccessToken` while `/admin` is gated on `Api:AdminToken`. They are two settings, and this
deployment gives them the same value, which is why one client reads both. A deployment that
sets them differently would answer 403 on the public read — costing the facility count and
nothing else, named in the run summary — and would need a second client built with the other
token.

## The backfill

`python -m stewards.metrics.backfill` recovers `datasets_crawled` for the months before the
capture started running. From the Actions tab it is **Backfill the crawl figure**, dispatch
only — there is no schedule, because once it has caught up the monthly job keeps the series
going.

It is safe to run twice: months already captured are left alone, and a month still in
progress is left to the monthly capture. It needs only the admin token.

## Adding a measurement

Add the field to the right list in `measurements.py` — `QUALITY_FIELDS`, `ACTIVITY_FIELDS`,
`SUMMARY_FIELDS` or `ANALYTICS_FIELDS` — and declare it on the response model if it is not
there already. A field that has to be computed rather than copied gets a function beside the
weighted completeness, and a test in `tests/unit/test_metrics_measurements.py` asserting it
at its boundaries.

Adding a whole new source is a read in `collect`, a builder, and an entry in the table above.

Nothing needs removing from the store when a measure is redefined: the rows are fields, and a
new definition is recomputed over the months already captured.
