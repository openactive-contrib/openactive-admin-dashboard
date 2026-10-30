"""The daily publisher notification job: monitors -> decision rules -> notification rows.

`python -m stewards.outreach` runs it. The decision tree is `rules.RULES`; everything else
is plumbing that does not change when a rule does. See docs/publisher-notification-job.md.
"""
