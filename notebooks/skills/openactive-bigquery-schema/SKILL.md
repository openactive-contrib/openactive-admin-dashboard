---
name: openactive-bigquery-schema
description: Where to find the exact table and column definitions of the BigQuery dataset openactive-monitor.openactive_analytics (feeds, opportunities, opportunity_ingestion, insight_run_summary, feed_insights, feed_quality, active_opportunities_summary and the other insight tables). Use before writing any SQL against that dataset, and whenever unsure whether a column exists or what type it is.
---

# OpenActive BigQuery schema

**Dataset:** `openactive-monitor.openactive_analytics`

The tables are written by `openactive-contrib/openactive-monitor`, and their schemas are
maintained in that repository. This skill does not copy them, so it cannot go stale. Read the
source before writing SQL. Never write column names from memory or guess them from OpenActive
vocabulary: the table has `startDate` and `region_name`, not `start_date` or `region`.

## 1. Read the published schema

**Ingested tables** (`feeds`, `feed_ingestion`, `opportunities`, `opportunity_ingestion`,
`insight_run_summary`, `custom_properties`). Each column's type and meaning, plus merge keys:

https://raw.githubusercontent.com/openactive-contrib/openactive-monitor/main/.claude/skills/bigquery-schemas/SKILL.md

**Insight tables** (`feed_insights`, `insight_category_counts`, `insight_sport_discipline`,
`insight_sport_discipline_master`, `feed_quality`, `active_opportunities_summary`, and again
`insight_run_summary` and `custom_properties`). One BigQuery JSON schema file per table, named
after the table:

https://raw.githubusercontent.com/openactive-contrib/openactive-monitor/main/jobs/opportunity-insights/schemas/<table>.json

The SQL that fills them, which defines what each count means, is in
https://raw.githubusercontent.com/openactive-contrib/openactive-monitor/main/jobs/opportunity-insights/queries.py

Fetch only the files the task needs. If you cannot fetch URLs, ask the user to attach them.
For the ingested tables, the first file is enough. Don't write SQL against a table whose
columns you have not seen.

## 2. If it disagrees with the warehouse, the warehouse wins

The documents can lag the tables. `opportunities.isAccessibleForFree` exists, for example,
but is not in the schema document. When a query fails on a column, or before relying on a
column you have not seen used, ask the live dataset from inside the notebook:

```python
cols = run_query(f"""
    SELECT table_name, field_path, data_type, description
    FROM `{DATASET}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`
    WHERE table_name = @table
    ORDER BY field_path
""", [bigquery.ScalarQueryParameter("table", "STRING", "opportunities")])
```

`INFORMATION_SCHEMA` queries scan almost nothing. `field_path` lists nested `RECORD` fields,
such as `custom_properties.property`. A `JSON` column appears as a single field; its inner
shape is in openactive-data-analysis.

To list the tables, which also catches one that neither document mentions:

```python
run_query(f"SELECT table_name, table_type FROM `{DATASET}.INFORMATION_SCHEMA.TABLES`")
```

## 3. Things the schema files do not spell out

- Names mix styles. `startDate`, `endDate`, `ageRange`, `has_superEvent`, `has_subEvent`,
  `accessibilitySupport`, `genderRestriction` and `isAccessibleForFree` are camelCase;
  everything else is snake_case. Copy them exactly.
- Several columns are `JSON`, not `STRING`. Read them with `JSON_VALUE`, `JSON_VALUE_ARRAY`,
  `JSON_QUERY_ARRAY` or `JSON_EXTRACT_ARRAY`. openactive-data-analysis gives the shapes.
- `opportunities.feed_id` and `opportunity_ingestion.feed_id` join to `feeds.id`.
  `opportunity_ingestion` calls the dataset column `dataset_id`; everywhere else it is
  `dataset_url`.
- `feeds` can hold several rows per `id`. Take the latest by `last_access`.
- Append-only logs: `feed_ingestion`, `opportunity_ingestion`, and the run-dated insight tables
  (`insight_run_summary`, `feed_insights`, `insight_category_counts`, `insight_sport_*`, all
  partitioned on `run_date`). Filter them to a run unless you want history.
- Current state only: `opportunities`, `feed_quality`, `active_opportunities_summary` and
  `custom_properties`.
