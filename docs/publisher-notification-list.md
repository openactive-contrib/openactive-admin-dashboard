---
title: Publisher notification list
---

# Publisher notification list

One row per publisher with open problems, kept by the stewards team. Unlike every other page,
these rows are **stored**: the daily batch does not rewrite them, and your edits are saved.

## What needs attention

A row is flagged, and sorts to the top, when its status is **Open** or **In progress** and:

- it has never been contacted, or
- the last contact was more than 5 days ago, or
- one of its problems has passed its Slack alert threshold: a dataset-wide stall at 5 days,
  a single-feed stall on a Slot or ScheduledSession feed at 10 days, or a feed ingestion
  error at 10 days.

The sidebar pill counts these rows. Closed rows never alert and are hidden unless you turn
on **Show closed**.

## Working a row

1. Select the row. Its problems, the reasons it is flagged and a draft email appear below.
2. Edit the draft if you need to and copy it into your mail client. The dashboard does not
   send email, and edits to the draft are not saved.
3. Under **Update this row**, set the status, stakeholder, contact and date contacted, add a
   note, and **Save**.
4. When the publisher has fixed everything, set the status to **Closed**.

Saving changes only those five fields; the problems on the row are kept exactly as they were.

## Deleting

**Delete row** removes the row and its problems and notes permanently, after you confirm.
Prefer **Closed** for a row that is finished: it keeps the history.
