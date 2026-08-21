"""Registry lookup helpers, tested once rather than per monitor."""

from __future__ import annotations

import pytest

from stewards.monitors.registry import (
    MONITOR_REGISTRY,
    Col,
    ColKind,
    Group,
    Monitor,
    Severity,
    get_monitor,
    groups,
    monitor_ids,
    monitors_in_group,
)


def test_registry_is_not_empty() -> None:
    assert MONITOR_REGISTRY


def test_get_monitor_round_trips() -> None:
    for monitor_id in monitor_ids():
        assert get_monitor(monitor_id).id == monitor_id


def test_get_monitor_rejects_an_unknown_id() -> None:
    with pytest.raises(KeyError, match="unknown monitor"):
        get_monitor("orphan_children")


def test_groups_are_in_registry_order_without_duplicates() -> None:
    result = groups()
    assert len(result) == len(set(result))
    assert all(any(m.group is g for m in MONITOR_REGISTRY) for g in result)


def test_monitors_in_group_filters() -> None:
    availability = list(monitors_in_group(Group.AVAILABILITY))
    assert availability == [m for m in MONITOR_REGISTRY if m.group is Group.AVAILABILITY]
    assert list(monitors_in_group(Group.COVERAGE)) == []


def test_column_lookup_by_label() -> None:
    monitor = get_monitor("single_feed_stall")
    assert monitor.column("Publisher").field == "publisher_name"
    with pytest.raises(KeyError):
        monitor.column("Nonexistent")


def test_crumb_names_the_group() -> None:
    assert get_monitor("http_failure").crumb == "Availability monitor"


def test_detail_column_paths() -> None:
    col = Col("detail.last_modified", "Last modified", ColKind.DATE)
    assert col.is_detail
    assert col.detail_attr == "last_modified"
    assert not Col("days_open", "Days", ColKind.DAYS).is_detail


def test_a_monitor_with_no_columns_is_rejected() -> None:
    with pytest.raises(ValueError, match="declares no columns"):
        Monitor(
            id="broken",
            name="Broken",
            group=Group.CONTENT,
            severity=Severity.MEDIUM,
            blurb="x",
            unit="things",
            columns=(),
        )
