"""Daily Slack digest of incidents that have just crossed a threshold.

Run by `.github/workflows/incident-alerts.yml` as `python -m stewards.alerts`. The rules are
`checks.CHECKS`; everything else here is shaping and transport. Streamlit-free.
"""
