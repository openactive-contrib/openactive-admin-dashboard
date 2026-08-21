---
name: testing
description: Test strategy and standards for this repo. Use before writing any test, and whenever a change lands without tests.
---

# Testing

Streamlit apps rot because logic gets buried in page scripts. The whole test strategy rests
on one rule: **anything that computes a value is importable without Streamlit.**

## Shape

```
tests/
  unit/          pure functions: transforms, threshold maths, registry, email drafting
  contract/      api client + repository against respx-mocked fixtures
  smoke/         AppTest-driven page renders
  fixtures/      JSON payloads per endpoint (+ _empty and _malformed variants)
```

## Coverage bar

- `src/stewards/{monitors,components,api}` ≥ 90%; project ≥ 80%.
- Coverage is a floor, not a goal. A module at 100% with no boundary cases is untested.

## What every pure function gets

1. Happy path with a realistic fixture.
2. Empty input — zero rows must render an empty table, never raise.
3. One boundary: `days_open == threshold` (must count as past threshold),
   score `0` and `100`, `future_count == 0`, a null optional field, a single-row series.

## Priority targets (write these first)

- **Threshold arithmetic** — `days_open`, `first_detected`, `past_threshold`. Its own module,
  parametrised across ≥ 8 cases including a snapshot gap (a missing daily run must not reset
  an incident's age).
- **Registry validation** — ids unique, every `Col.field` exists in that monitor's fixture,
  every registry entry has a page module, groups are known values. One test that iterates the
  whole registry catches every future monitor for free.
- **DataFrame shaping** — declared columns, declared order, declared dtypes.
- **Email draft rendering** — golden-file test; the copy is publisher-facing.
- **Contact queue union** — an incident present in two monitors appears once per monitor with
  correct days_open, sorted oldest first.

## API tests

`respx` only, never the network:

```python
@respx.mock
def test_incidents_paginates():
    respx.get(url__regex=r".*/incidents.*").mock(side_effect=[page1, page2])
    result = _fetch_incidents("single_feed_stall")
    assert len(result.data) == 137
```

Required per endpoint: 200, 200-empty, 401, 500, timeout, malformed payload, two-page
pagination. Assert on pydantic models.

## Smoke tests

`streamlit.testing.v1.AppTest` with the repository layer monkeypatched to fixtures:
every page renders, no exception, expected number of metrics and one dataframe. Cheap
insurance against a broken import in a page nobody opened.

## Rules

- No network, no real API token, no BigQuery in any test.
- No `time.sleep`; freeze time with a fixture, never `datetime.now()` inside logic — pass
  `today` as an argument so it is testable.
- Fix a data bug → add a regression test using that exact payload, named after the bug.
- Deleting a test to make CI green is never the fix.
