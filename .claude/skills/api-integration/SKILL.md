---
name: api-integration
description: Rules for talking to the stewards REST API — client, models, repository layer, caching, error handling. Use when adding or changing any data fetch.
---

# API integration

The app is a **client only**. All data comes from the stewards REST API (which fronts the
daily BigQuery batch). No BigQuery SDK, no SQL, no direct database access in this codebase.

## Three layers, never skipped

```
api/client.py      httpx transport: base URL, auth header, timeout, retry, raise_for_status
api/models.py      pydantic v2 models mirroring the contract exactly
api/repository.py  one typed function per endpoint; the ONLY module importing client.py
```

Pages and components import `repository`. Nothing else.

## Client rules

- One module-level `httpx.Client` with `base_url`, `timeout=httpx.Timeout(10.0, connect=3.0)`,
  and `transport=httpx.HTTPTransport(retries=2)`.
- Auth: `Authorization: Bearer <STEWARDS_API_TOKEN>` from secrets. The user's Google identity
  gates the app; it is not the API credential. Never log the token.
- Every response is parsed into a model. A `ValidationError` is a bug in either the contract
  or the API — surface it as `st.error("The monitoring API returned an unexpected shape")`
  plus a logged detail, never a silent empty table.
- Map failures to typed exceptions: `ApiUnavailable` (5xx/timeout), `ApiUnauthorized` (401/403),
  `ApiContractError` (validation). Pages render a distinct message per type.

## Envelope

Every list endpoint returns:

```json
{ "data": [ ... ],
  "meta": { "snapshot_date": "2026-08-21", "generated_at": "2026-08-21T06:12:04Z",
            "total": 23, "page": 1, "page_size": 100 } }
```

`meta.snapshot_date` is displayed in the page header. Never render a table without it.

## Endpoints (see BUILD_BRIEF.md §3 for full contract)

```
GET /api/v1/summary                              overview KPIs + per-monitor tile counts
GET /api/v1/monitors                             registry as served by the API
GET /api/v1/monitors/{id}/incidents              ?past_threshold=&search=&page=&page_size=
GET /api/v1/monitors/{id}/trend?days=30          total open + past-threshold series
GET /api/v1/contact-queue                        cross-monitor, days_open >= threshold
GET /api/v1/quality?expand=feeds                 publisher quality table
GET /api/v1/coverage/active-places               rows + match-rate histogram buckets
GET /api/v1/feeds/{feed_id}/schema-diff          added/removed/changed field list
```

Pagination: request `page_size=500`; if `meta.total` exceeds what you fetched, page until
exhausted inside the repository function — pages never loop.

## Caching

`@st.cache_data(ttl=3600)` wraps the repository function, and the wrapped body must be a
plain function importable and callable in tests without Streamlit:

```python
def _fetch_incidents(monitor_id: str, *, past_threshold: bool) -> IncidentPage: ...


@st.cache_data(ttl=3600, show_spinner="Loading…")
def fetch_incidents(monitor_id: str, *, past_threshold: bool = False) -> IncidentPage:
    return _fetch_incidents(monitor_id, past_threshold=past_threshold)
```

Tests call `_fetch_incidents`. Data changes once a day — cache generously; no refresh button,
just the snapshot caption.

## Testing

- `respx` mocks every call; record fixtures under `tests/fixtures/` from the real API and
  anonymise publisher names only if the payload is sensitive.
- Required cases per endpoint: 200 happy path, 200 empty `data`, 401, 500, timeout,
  malformed payload (missing required field), and a paginated response spanning two pages.
- Assert on models, not on raw dicts.
- Never mark a network test `xfail`; if it needs the network, it is the wrong test.
