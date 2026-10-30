---
title: Publisher notification job
---

# Publisher notification job

Every day at 08:07 UK time a scheduled job reads the monitors, decides which problems the
stewards team should raise with a publisher, and records them on the
[publisher notification list]({% link publisher-notification-list.md %}). It sends no email:
it fills the list, and a steward does the contacting.

The job is `python -m stewards.outreach`, run by `.github/workflows/publisher-notifications.yml`.
The decision tree is `src/stewards/outreach/rules.py`.

## What it raises

A problem is raised when one of these rules triggers on an open incident:

| Rule | Monitor | Raised when | Not raised if, on the same dataset |
|---|---|---|---|
| `http_endpoint_failure` | Feed ingestion errors | open 10 days or more | |
| `dataset_stall` | Dataset-wide stalls | open 5 days or more | |
| `single_feed_stall` | Single-feed stalls | open 10 days or more, on a Slot or ScheduledSession feed | |
| `orphaned_children` | Orphaned child items | 20% or more of child items orphaned | `http_endpoint_failure`, then `single_feed_stall` |
| `future_decline` | Future session decline | open 15 days or more, with a ScheduledSession feed declining | `http_endpoint_failure`, then `dataset_stall`, then `single_feed_stall` |

The last column is the rule's **pre-checks**. When one of those rules has also triggered on
the same dataset today, that earlier problem is the one to raise: it probably explains this
one. Only a pre-check that *triggered* holds a problem back. An incident still below its own
threshold does not.

Thresholds are "or more", not "exactly", so a day the job did not run is caught up the next.

## What it writes

The list holds **one active row per publisher**. Active means Open or In progress.

- A publisher with no active row gets a new **Open** row, with an id of the form
  `pn_<publisher>-<YYYYMMDD>`.
- A publisher with an active row has the new problem appended to it. If there is more than
  one active row, the problem goes on the oldest.
- A problem already on any active row is not raised again.
- A problem whose publisher the API could not name (`pub_unknown`) is not raised. It is
  listed in the run summary instead, because there is nobody to contact.
- Closed rows are never read or changed. A problem that comes back after its row was closed
  is raised on a new row.

On every active row, the job also brings each problem it recognises up to date:

- If the incident is still open, today's figures replace the stored ones. The rule and the
  date the problem was first recorded are kept.
- If the incident has closed, the problem is removed. When that leaves the row with no
  problems, the job adds the note "All problems resolved as of <date>; review and close." It
  does not change the status: closing a row is a steward's decision.

Problems the job does not recognise are kept exactly as stored. That covers problems for a
monitor the rules do not read, and anything with no feed or dataset to identify it by. The
job never changes the status, stakeholder, contact or date-contacted fields.

A problem is identified by its monitor and its feed, or by its monitor and its dataset URL
when it is a dataset-level problem. This applies to rows a steward typed in by hand as well.

## When it writes nothing

The job writes nothing, and the run fails, when any of these happen:

- a monitor cannot be read;
- a monitor's latest snapshot is not today's, which means the batch has not landed yet;
- a monitor or the notification list did not arrive in full.

Removing problems because of a partial read would be worse than not running. A failed gate is
not retried. The next morning's run picks everything up.

If an individual write is rejected, the job carries on with the others and the run fails at
the end. The writes that succeeded stay written.

Each run writes a summary to the workflow run page. It lists the rows created and updated,
and every problem that was found but not raised, with the reason. It never includes contact
addresses.

## Running it by hand

From the Actions tab, run **Publisher notifications**. **dry_run** is on by default: the run
prints the rows it would write and writes nothing. Locally:

```bash
ADMIN_API_BASE_URL=… ADMIN_API_TOKEN=… uv run python -m stewards.outreach --dry-run
```

The workflow needs the same `ADMIN_API_BASE_URL` and `ADMIN_API_TOKEN` repository secrets as
the incident alerts.

## Changing the rules

- **A threshold or a pre-check:** edit the rule's entry in `RULES`.
- **A new notification path:** add a `Rule` to `RULES`. Give it an `id` (it is stored on
  every problem it raises), the `monitor_id` it reads, its conditions, and its pre-checks in
  the order the decision tree checks them. `validate` refuses a rule with an unknown monitor,
  an unknown pre-check, or a pre-check cycle.
- **A new kind of condition:** add a small class with `holds(monitor, incident)` and
  `describe()` next to `AtLeast`, `OneOf` and `AnyItem`.

A condition reads its field with the registry's path syntax: `days_open`, `feed_type`, or
`detail.<field>` through the monitor's detail model. A new monitor's incidents need a
`dataset_url` for pre-checks to link them. A feed-level incident also needs a `feed_id`.

Add a test for the rule's threshold in `tests/unit/test_outreach_rules.py`.
