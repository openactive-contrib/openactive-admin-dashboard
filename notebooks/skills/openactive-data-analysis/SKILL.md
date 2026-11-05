---
name: openactive-data-analysis
description: What the OpenActive data in BigQuery (openactive-monitor.openactive_analytics) means and how to count it correctly — publishers, datasets, feeds, opportunity kinds, parent/child rows, "future" opportunities, regular vs preview feeds, the JSON column shapes, geography, and the pre-computed insight tables. Use whenever writing SQL or interpreting results for an OpenActive analysis.
---

# Analysing OpenActive data in BigQuery

The tables are written by the `openactive-monitor` pipeline: a daily batch crawls every
OpenActive RPDE feed and upserts what it finds. Column names and types come from
openactive-bigquery-schema. This skill covers what that schema cannot: what a row stands for,
how the JSON columns are shaped, and how to count without double-counting. The idioms below
are the ones the pipeline's own insight job uses
(`jobs/opportunity-insights/queries.py` in `openactive-contrib/openactive-monitor`). When a
notebook's figure should agree with a published one, read that file and match its definition.

`DATASET` below is `openactive-monitor.openactive_analytics`.

## The entities

- **Publisher**: an organisation publishing open data (`feeds.publisher_name`). A publisher can
  have more than one dataset.
- **Dataset**: one dataset site, keyed by `dataset_url`. It is the unit a publisher manages.
- **Feed**: one RPDE endpoint inside a dataset, keyed by `feeds.id`, with `feeds.type` naming
  what it carries. `opportunities.feed_id` and `opportunity_ingestion.feed_id` join to
  `feeds.id`.
- **Opportunity row**: one item from a feed (`opportunities`), keyed by
  `(dataset_url, feed_id, id)`. This is current state. Deleted items are removed, so there is
  no history in this table.

`feeds` can hold more than one row per `id`. Always take the latest:

```sql
SELECT id AS feed_id, dataset_url, dataset_name, publisher_name, type AS feed_type, is_regular
FROM `DATASET.feeds`
QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY last_access DESC) = 1
```

For dataset-level attributes (publisher, provider), dedupe by `dataset_url` the same way. Joining
`opportunities` to un-deduped `feeds` fans rows out and inflates every count.

**Regular vs preview.** `feeds.is_regular = FALSE` marks preview feeds: test or onboarding data
that is not in the public catalogue. `opportunities` has no such column, so join to `feeds`.
Default to regular feeds only and say so. Include preview only when asked.

## Kinds, and counting without double-counting

`opportunities.kind` is the item's `@type`. Run `SELECT kind, COUNT(*) ... GROUP BY kind`
before assuming the set of values. The ones that matter:

| Role | Kinds | `startDate` |
|---|---|---|
| Parent (what is offered) | `SessionSeries`, `FacilityUse`, `IndividualFacilityUse` | `SessionSeries`: the earliest `eventSchedule` start if it has one, otherwise usually NULL. Facility rows: NULL |
| Child (when it happens) | `ScheduledSession` (child of `SessionSeries`), `Slot` (child of a facility) | Yes |
| Standalone | `Event`, `OnDemandEvent`, `CourseInstance`, `HeadlineEvent` | Usually |

The child points at its parent through `has_superEvent`. **Decide the unit before writing the
query, and name it in the output.**

- *"How many sessions or bookable slots"* → count child rows with a `startDate`.
- *"How many things are offered"* → count parents. A `SessionSeries` is one class; a
  `FacilityUse` is one bookable facility.
- *"Activity available"*, the headline figure the pipeline publishes, uses the **narrow** count:
  every non-`Slot` row, plus each distinct facility referenced by a `Slot`, instead of the
  `Slot`s themselves. A sports hall with 300 hourly slots then counts once, not 300 times. See
  `active_opportunities_summary_narrow_counts` in the insight job.

**Inline subEvents.** Some publishers embed a series' sessions inside the parent instead of
publishing them as rows. They sit in `has_subEvent` as JSON objects, not URI strings, and are
not rows of their own. The pipeline adds them to opportunity counts:

```sql
SELECT o.dataset_url, o.feed_id, subevent
FROM `DATASET.opportunities` AS o,
     UNNEST(JSON_EXTRACT_ARRAY(o.has_subEvent)) AS subevent
WHERE LOWER(JSON_TYPE(subevent)) = 'object'
-- start: COALESCE(JSON_VALUE(subevent, '$.startDate'), JSON_VALUE(subevent, '$.dateStart'))
```

If a count leaves them out, say so in the notes.

## "Future" and time

- Future means `startDate >= TIMESTAMP(CURRENT_DATE())`, from midnight UTC today. That
  includes sessions earlier today, which matches the pipeline. Next 7 days means also
  `startDate < TIMESTAMP(DATE_ADD(CURRENT_DATE(), INTERVAL 7 DAY))`. Use a parameter for the
  reference date when the user might want another.
- `startDate` and `endDate` are UTC `TIMESTAMP`s. A date with no time was stored at midnight
  UTC. Convert to `Europe/London` for anything shown by hour or weekday:
  `EXTRACT(HOUR FROM startDate AT TIME ZONE 'Europe/London')`.
- `opportunities` is a snapshot of now. For change over time, use the run-dated tables
  (`insight_run_summary`, `feed_insights`, `insight_category_counts`) or
  `opportunity_ingestion.total_future_opportunities` per run. Don't reconstruct history from
  `modified` or `last_updated`.

## Inheritance: use the extracted columns

At ingest, a child with no value of its own inherits it from its parent: `activity`,
`facility`, `location` and the geography columns, `startDate`/`endDate`, `ageRange`, `level`,
`accessibilitySupport`, `genderRestriction` and `organization_name`. The extracted columns
already hold the resolved value. `json_data` is only the item's own payload, so
`JSON_VALUE(json_data, '$.activity...')` misses everything a session gets from its series. Use
`json_data` only for a property with no column. `inherited_data` holds the parent's
properties. `organization_json` is deliberately **not** inherited.

## JSON column shapes

| Column | Shape | Read it with |
|---|---|---|
| `activity` | array of label strings, e.g. `["Yoga","Pilates"]` (the `prefLabel`s; activity-list ids are not kept) | `UNNEST(JSON_VALUE_ARRAY(o.activity)) AS activity` |
| `facility` | array of facility-type label strings | `UNNEST(JSON_VALUE_ARRAY(o.facility))` |
| `accessibilitySupport` | array of label strings, or NULL | `UNNEST(JSON_VALUE_ARRAY(o.accessibilitySupport))` |
| `location` | object: `latitude`, `longitude`, `postal_code`, sometimes `address` or `place_name` (`place_name` alone means it could not be geocoded) | `SAFE_CAST(JSON_VALUE(location, '$.latitude') AS FLOAT64)` |
| `ageRange` | the OpenActive `QuantitativeValue` as published: `minValue` and/or `maxValue`, either may be missing | `SAFE_CAST(JSON_VALUE(ageRange, '$.minValue') AS INT64)` |
| `level` | as published: array of strings or of `{prefLabel}` objects | `COALESCE(JSON_VALUE(l), JSON_VALUE(l, '$.prefLabel'))` over `UNNEST(JSON_QUERY_ARRAY(level)) AS l` |
| `has_superEvent` | usually an array of parent URI strings; sometimes one string, or an inline object with `@id` | the normalisation below |
| `has_subEvent` | array of URI strings and/or inline session objects | see "Inline subEvents" |
| `json_data` | the full item as published | `JSON_VALUE(json_data, '$.path')`, expensive |

`genderRestriction` is a `STRING` URI. Use `REGEXP_EXTRACT(genderRestriction, r'[^/#]+$')` to get
`FemaleOnly` / `MaleOnly` / `NoRestriction`; NULL means not stated.

Normalising a child's parent reference:

```sql
SELECT o.id,
  CASE JSON_TYPE(ref)
    WHEN 'object' THEN COALESCE(JSON_VALUE(ref, '$."@id"'), JSON_VALUE(ref, '$.id'))
    WHEN 'string' THEN JSON_VALUE(ref)
  END AS parent_ref
FROM `DATASET.opportunities` AS o
LEFT JOIN UNNEST(
  CASE JSON_TYPE(o.has_superEvent)
    WHEN 'array' THEN JSON_QUERY_ARRAY(o.has_superEvent)
    ELSE [o.has_superEvent]
  END) AS ref
```

`parent_ref` matches the parent row's `data_id`. It does not match `id`, which is the RPDE item
id and often differs.

**Labels are free text.** Activity, facility and accessibility values are the publishers'
labels. The same activity appears as `Yoga`, `yoga ` and `Hatha Yoga`. Group on
`LOWER(TRIM(x))` and say that you did. Don't claim the labels map to the OpenActive activity
list: the ids are not stored.

**Activities belong to activity kinds.** `FacilityUse`, `IndividualFacilityUse` and `Slot` rows
describe a facility in `facility` and usually have an empty `activity`; every other kind uses
`activity`. Filter on the kind, as the pipeline's `is_activity` does, before you count:
`NOT COALESCE(o.kind IN ('FacilityUse', 'IndividualFacilityUse', 'Slot'), FALSE)`.
Otherwise facility rows inflate the denominator of every activity share.

**One opportunity, several labels.** `activity` is a list, so "% of opportunities with
activity X" adds up to more than 100%. Say so next to the figures. De-duplicate labels per
row after normalising, or a row carrying `Yoga` and `yoga ` counts twice:

```sql
ARRAY(
  SELECT DISTINCT LOWER(TRIM(label))
  FROM UNNEST(JSON_VALUE_ARRAY(o.activity)) AS label
  WHERE TRIM(label) != ''
) AS activities
```

Use the number of opportunities as the denominator, not the number of labels, and count the
opportunities with no label at all.

## Geography

Geography is resolved from `location` at ingest. It is NULL when an item has no usable
coordinates or postcode, so always report coverage ("x% of rows have a region") next to a
geographic breakdown.

- `region_name` / `region_code`: English regions, plus Scotland, Wales and Northern Ireland.
  "London" is `region_name = 'London'`: the 32 boroughs and the City of London. Don't guess
  the other names. List them with `SELECT region_name, COUNT(*) ... GROUP BY region_name`,
  which reads one small column, and make the notebook fail loudly if a region parameter
  matches nothing.
- `district_name` / `district_code`: local authority district (`LAD24`).
- `country_name` / `country_code`.
- `nhstrust_name` / `nhstrust_code`: NHS trust area.

Use the `*_code` columns for joins and the `*_name` columns for display. For maps, plot
latitude/longitude points (`plotly.express.scatter_map`). Boundary files are not in BigQuery.

## Pre-computed tables: read these first

The insight job recomputes these from `opportunities` on every run. They are small and cheap,
and they agree with what the team publishes. Check whether one already answers the question
before scanning `opportunities`. Their columns are in openactive-bigquery-schema.

| Table | Grain | Use for |
|---|---|---|
| `insight_run_summary` | one row per run (`run_date`) | fleet headlines over time: publishers, datasets, feeds, items, future items, regular/preview splits |
| `feed_insights` | feed × run | per-feed counts over time, latest ingestion `status` |
| `insight_category_counts` | run × `category` × `scope` × `value` | top activities, facilities, kinds, organisers, accessibility labels, for `scope` all / regular / preview |
| `active_opportunities_summary` | district × publisher × activity-or-facility, current | where future activity is: `opportunity_count`, `opportunity_count_narrow`. **Includes preview feeds** and has no column to exclude them, so a regular-only figure must come from `opportunities` |
| `feed_quality` | feed, current | completeness of key fields, `grade`, `score` |
| `insight_sport_discipline`, `insight_sport_discipline_master` | run × Sport England sport/discipline | how activity labels match Sport England's recognised sports |
| `custom_properties` | feed, current | properties published outside the OpenActive vocabulary |
| `opportunity_ingestion` | feed × run | crawl health: `status`, `error_code`, `total_future_opportunities` |

The run-dated tables are partitioned on `run_date`, so filter on it. For the latest run:
`WHERE run_date = (SELECT MAX(run_date) FROM ...)`. For the latest status per feed:
`QUALIFY ROW_NUMBER() OVER (PARTITION BY feed_id ORDER BY ingestion_date DESC) = 1`.

## Reporting checklist

- [ ] The unit is named: rows, sessions, slots, facilities, series, or the narrow count.
- [ ] Regular-only or including preview is stated.
- [ ] Future is defined, with its reference date.
- [ ] Parent/child double-counting is handled, and inline subEvents are included or excluded
      deliberately.
- [ ] Missing values (no region, no activity, no age range) are counted and shown, not
      silently dropped.
- [ ] Free-text labels are normalised, and the normalisation is stated.
- [ ] The data's date is given, and it is never described as live.
