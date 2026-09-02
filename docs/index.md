---
title: Documentation
---

Reference material for the OpenActive Data Stewards dashboard — the internal view of feed
health across the OpenActive publishers. The dashboard reports what is broken; these pages
say what to do about it.

## Contributing

- [Adding a dashboard]({% link adding-a-dashboard.md %}) — the end-to-end procedure for a
  new monitor: registry entry, page, home-page card, API contract, sample data and tests.

## Runbooks

- [Single-feed stalls]({% link single-feed-stalls-runbook.md %}) — a feed whose `modified`
  timestamp stops advancing while the endpoint still returns 200.

Figures in the dashboard come from a daily BigQuery batch, so every page there states the
snapshot date it was built from.
