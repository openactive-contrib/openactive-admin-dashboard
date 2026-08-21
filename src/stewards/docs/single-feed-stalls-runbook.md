---
title: Runbook — single-feed stalls
tags: [runbook, stalls, availability]
owner: Data Infrastructure
updated: 2026-08-14
sensitivity: restricted
---

A feed is **stalled** when its most recent modified timestamp has not advanced across
consecutive daily snapshots while the endpoint continues to return 200. Stalls are the most
common publisher-side failure and are almost always caused by an upstream export job rather
than by the feed itself.

## Detection

The nightly job compares `max(modified)` per feed against the previous snapshot. A feed with
no advance for two or more snapshots opens an incident. The incident carries its own age, so
the 7-day contact threshold is measured from first detection rather than from the last run —
a missing daily batch does not reset the clock.

Runs shorter than two snapshots are suppressed. That is what keeps a single slow export out
of the queue.

## Triage sequence

1. Confirm the stall is not a genuine seasonal pause — check whether the future opportunity
   count is also falling.
2. Check the dataset-wide stall monitor. If every feed for the publisher is stalled, treat it
   as one incident rather than several.
3. Re-run the feed fetch manually and compare the raw payload against the stored snapshot.
4. At day 7 the incident enters the contact queue. Use the draft email action and record the
   contact date.

Do not chase a stall inside the first 48 hours. Roughly a third of them self-resolve when the
publisher's next scheduled export runs.

## Contact threshold

The threshold is 7 days and is configured once, in `STEWARDS_CONTACT_THRESHOLD_DAYS`. The
dashboard displays the threshold on every monitor page so the number in the header always
matches the number driving the queue.

Contact history comes from the API. Where the API exposes no write endpoint, "last contacted"
renders as an em dash — that means unknown, not "never contacted".

## Known false positives

- Publishers who genuinely pause over a school holiday and resume unchanged.
- Feeds whose export writes a fixed `modified` value on every item, so the maximum never
  advances even when content changes.
- Datasets mid-migration to a new endpoint, where the old feed is frozen but still served.

Log each of these in the false positives register before closing the incident, so the same
feed is not re-triaged next week.
