"""Today's incidents for the monitors the rules read, and what the rules make of them. Pure.

Two passes. The first asks every rule of every incident whether it triggers. The second
applies pre-checks: a finding is suppressed when a rule it pre-checks also triggered on the
same dataset today. Only *triggered* findings suppress — an incident still below its own
threshold is not yet a problem to raise, so it does not hold another one back.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from stewards.api.models import Incident
from stewards.outreach.keys import dataset_url, incident_key
from stewards.outreach.rules import RULES, Rule


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Every open incident of each monitor the job manages, as read today."""

    incidents: Mapping[str, tuple[Incident, ...]]

    @property
    def monitor_ids(self) -> frozenset[str]:
        """The monitors read in full today: a stored problem of any other is left alone."""
        return frozenset(self.incidents)

    def by_key(self) -> dict[str, Incident]:
        """Open incidents by key, whether or not any rule triggers on them today."""
        indexed: dict[str, Incident] = {}
        for incidents in self.incidents.values():
            for incident in incidents:
                key = incident_key(incident)
                if key is not None:
                    indexed.setdefault(key, incident)
        return indexed


@dataclass(frozen=True, slots=True)
class Finding:
    rule: Rule
    incident: Incident
    key: str | None
    dataset_url: str | None
    suppressed_by: str | None = field(default=None)

    @property
    def raised(self) -> bool:
        return self.suppressed_by is None and self.key is not None


def decide(snapshot: Snapshot, rules: Sequence[Rule] = RULES) -> tuple[Finding, ...]:
    """Every incident a rule triggers on, in rule order, with its pre-check outcome."""
    triggered = [
        Finding(rule, incident, incident_key(incident), dataset_url(incident))
        for rule in rules
        for incident in snapshot.incidents.get(rule.monitor_id, ())
        if rule.triggers(incident)
    ]
    fired = _datasets_by_rule(triggered)
    return tuple(_apply_prechecks(finding, fired) for finding in triggered)


def _datasets_by_rule(findings: Iterable[Finding]) -> Mapping[str, frozenset[str]]:
    fired: defaultdict[str, set[str]] = defaultdict(set)
    for finding in findings:
        if finding.dataset_url is not None:
            fired[finding.rule.id].add(finding.dataset_url)
    return {rule_id: frozenset(urls) for rule_id, urls in fired.items()}


def _apply_prechecks(finding: Finding, fired: Mapping[str, frozenset[str]]) -> Finding:
    """The first pre-check, in the rule's order, that fired on the same dataset.

    A finding with no dataset URL cannot be linked to anything, so it is never suppressed:
    raising a problem twice is recoverable, silently never raising it is not.
    """
    url = finding.dataset_url
    if url is None:
        return finding
    for precheck in finding.rule.prechecks:
        if url in fired.get(precheck, frozenset()):
            return Finding(finding.rule, finding.incident, finding.key, url, precheck)
    return finding
