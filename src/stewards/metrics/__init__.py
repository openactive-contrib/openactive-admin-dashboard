"""The monthly metric capture: read the day's figures, store them as a month's row.

The endpoints behind the KPI framework report current state and keep no history, so a month
that nobody records cannot be recovered later. This job records one. `measurements.py` turns
each response into rows; the runner fetches, assembles and POSTs them to the metric snapshot
store on the API.

What is stored are source fields, never finished measures: `completeness_location`, not
"KPI4". A measure whose definition changes can then be recomputed for every month already
captured. See the metric snapshots section of the API's `docs/admin-api.md`.

No Streamlit in here, like the other two jobs.
"""
