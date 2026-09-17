---
title: Feed data quality
tags: [runbook, monitors, quality]
owner: Data Infrastructure
updated: 2026-09-16
sensitivity: internal
---

What the **Feed data quality** dashboard reports, and what to do about a feed it flags.

Unlike the availability monitors, this one is not a queue. It is the nightly assessment of
every feed in the fleet, and most of what it shows is context rather than a fault to chase.
Work it when you are choosing which publishers to approach about data quality, not when
something has broken — a broken feed is reported by the stall and ingestion monitors.

## What the check does

Each night the assessment reads every feed the crawl reached and records, per feed:

- a **status** — `OK`, `WARNING` or `ERROR` — for what the crawl itself found;
- a **quality score** out of 100 and a **grade** (Gold, Silver, Bronze), where it could
  produce one;
- the **completeness** of each recommended field, as the share of items carrying it;
- how many **future opportunities** the feed holds;
- the **required fields** missing from each opportunity type, and any errors or warnings.

It reports one snapshot. There is no quality history behind it, so nothing on the page
describes a trend, and the card states a level rather than a direction.

## Reading the page

The five figures across the top describe the fleet the batch assessed, not the rows below
them — a filter narrows the table and leaves the figures alone. A figure the batch did not
compute reads as an em dash; that is not a zero.

The four charts break the same snapshot down: how the scored feeds spread across the score
bands, which recommended fields are thin across the whole fleet, and how the feeds split by
crawl status and by grade.

The table is one row per feed, **grouped by dataset**: a dataset's feeds are consecutive, and
the datasets are ordered by their mean score, best first. A feed the assessment could not
score sits after every scored one rather than at the bottom of the scale. Select a row to
read what the assessment said about that feed — its errors and warnings, the required fields
it is missing, and its coverage field by field.

## Triage

1. **`ERROR` first.** An error means the crawl could not read the feed properly — a parse
   failure, or an endpoint that did not return usable items. Check whether the feed also
   appears under **Feed ingestion errors**: if it does, that monitor owns it and this page is
   only reporting the consequence.
2. **Then a whole dataset scoring low.** Because the table is grouped, a publisher whose
   every feed sits at the bottom is visible as a block. That is a publishing-pipeline
   conversation, not a per-feed one.
3. **Then the thin recommended fields.** The completeness chart says which fields the fleet
   as a whole is missing. A field that is thin everywhere is usually a gap in a booking
   system's export, and is better raised with the system vendor than with each publisher.
4. **`WARNING` last.** A warning is usually "no future opportunities scheduled", which is
   seasonal for many publishers and not by itself a fault.

## What to tell a publisher

This page drafts no email, because a low score is rarely a single actionable fault. Say what
the assessment found, in its terms:

- name the feed and what its status is;
- for an error, quote the error line the assessment recorded;
- for a missing required field, name the opportunity type and the field, because that is what
  a consumer cannot render;
- for thin coverage, name the one or two fields that would move the score most, rather than
  sending the whole list.

Do not quote the score itself as a target. It is a composite the publisher cannot act on
directly, and it moves when the assessment changes.

## When to escalate

- A feed that has been `ERROR` across consecutive snapshots while the ingestion monitor
  reports it healthy — the two disagree, and the assessment may be wrong.
- The fleet average moving sharply between two snapshots with no release behind it: that is
  an assessment change, not a fleet change, and it belongs with whoever owns the batch.
- A whole booking system's feeds losing a grade at once, for the same reason.

## Data

`GET /admin/feed-quality` (interim admin API) or `GET /api/v1/monitors/feed_quality/quality`,
query `monitor_feed_quality_v1`. One request, one snapshot, no trend endpoint — see
[Adding a dashboard]({% link adding-a-dashboard.md %}) for the payload contract.
