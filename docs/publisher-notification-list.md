---
title: Publisher notification list
---

# Publisher notification list

One row per publisher with open problems, kept by the stewards team. Unlike every other page,
these rows are **stored**: the daily batch does not rewrite them, and your edits are saved.

Rows are created and kept up to date by the
[publisher notification job]({% link publisher-notification-job.md %}), which runs at 08:07
each day. It adds new problems, refreshes the figures on the ones already listed, and removes
problems whose incident has closed. On an **In progress** row it does not remove a closed
problem straight away: it marks it resolved, so you can tell the publisher (see below). It
never changes the status, responsible person, contact or date contacted, and it leaves the
notes on a new row empty for you.

The **Publisher** name links to the publisher's dataset site and opens it in a new tab.
**Created** is when the row was added, in UK time. The last column, **Problems (JSON)**, is
the row's problems exactly as stored; click a cell to expand it.

## What needs attention

A row is flagged, and sorts to the top, when its status is **Open** or **In progress** and:

- it has never been contacted, or
- the last contact was more than 5 days ago, or
- one of its problems has passed its Slack alert threshold: a dataset-wide stall at 5 days,
  a single-feed stall on a Slot or ScheduledSession feed at 10 days, or a feed ingestion
  error at 10 days, or
- one of its problems has been resolved since the publisher was last contacted. This applies
  to **In progress** rows only. The reason reads "<monitor> resolved on <date>: let the
  publisher know", and the problem's **Resolved** column shows the date.

A flagged row's **Needs attention** cell is red. It names each kind of reason once: **Never
contacted**, **Follow up**, **Past threshold** or **Tell publisher**. Select the row to see
the full reasons. The sidebar pill counts these rows. Closed rows never alert and are hidden unless you turn
on **Show closed**.

## Working a row

1. Select the row. A pop-up opens with its problems, the reasons it is flagged and a draft
   email. Close it with the **×** or by clicking outside it.
2. Edit the draft if you need to and copy it into your mail client. The dashboard does not
   send email, and edits to the draft are not saved. If the row has a decline in future
   sessions, first check the decline isn't seasonal (for example winter activities); the
   dashboard shows a reminder.
3. Under **Update this row**, set the status, the person responsible, the contact and the
   date contacted, add a note, and press **Save** at the bottom right of the pop-up. The
   pop-up stays open and says the row was saved.
4. When the publisher has fixed everything, set the status to **Closed**. A row whose
   problems have all cleared is not closed for you: the job adds a note saying so.

Saving changes only those five fields; the problems on the row are kept exactly as they were.

## Deleting

**Delete row** (red, beside **Save**) removes the row and its problems and notes permanently,
after you confirm inside the pop-up. Prefer **Closed** for a row that is finished: it keeps
the history.
