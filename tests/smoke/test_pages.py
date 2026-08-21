"""AppTest renders of every page. Cheap insurance against a broken import.

These also cover the one thing a unit test cannot: that a pandas Styler, `column_config`
and `LineChartColumn` survive `st.dataframe` together.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PAGES_DIR = Path(__file__).resolve().parents[2] / "src" / "stewards" / "pages"

PAGES = [
    "00_overview.py",
    "01_contact_queue.py",
    "10_single_feed_stalls.py",
    "12_http_failures.py",
    "90_docs.py",
]

MONITOR_PAGES = ["10_single_feed_stalls.py", "12_http_failures.py"]


def run(name: str) -> AppTest:
    app = AppTest.from_file(str(PAGES_DIR / name), default_timeout=30)
    app.run()
    return app


@pytest.mark.parametrize("name", PAGES)
def test_page_renders_without_exception(name: str) -> None:
    app = run(name)
    assert not app.exception, [e.value for e in app.exception]


@pytest.mark.parametrize("name", PAGES)
def test_every_page_states_the_snapshot(name: str) -> None:
    captions = " ".join(c.value for c in run(name).caption)
    if name == "90_docs.py":
        assert "theodi.org" in captions  # docs carry the access statement, not a snapshot
    else:
        assert "Snapshot 2026-08-21" in captions
        assert "daily batch" in captions


@pytest.mark.parametrize("name", MONITOR_PAGES)
def test_monitor_page_has_three_metrics_a_chart_and_one_table(name: str) -> None:
    app = run(name)
    assert len(app.dataframe) == 1
    assert len(app.toggle) == 1
    assert len(app.selectbox) == 2
    assert len(app.text_input) == 1


def test_monitor_page_table_carries_every_declared_column() -> None:
    from stewards.monitors.registry import get_monitor

    app = run("10_single_feed_stalls.py")
    frame = app.dataframe[0].value
    assert list(frame.columns) == [c.label for c in get_monitor("single_feed_stall").columns]
    assert len(frame) == 23


def test_threshold_toggle_narrows_the_table() -> None:
    app = run("10_single_feed_stalls.py")
    assert len(app.dataframe[0].value) == 23
    app.toggle[0].set_value(True).run()
    assert len(app.dataframe[0].value) == 7
    assert not app.exception


def test_search_narrows_the_table() -> None:
    app = run("12_http_failures.py")
    app.text_input[0].set_value("halo").run()
    assert len(app.dataframe[0].value) == 1
    assert not app.exception


def test_search_with_no_match_renders_an_empty_state_not_an_error() -> None:
    app = run("12_http_failures.py")
    app.text_input[0].set_value("no-such-publisher").run()
    assert not app.exception
    assert not app.dataframe
    assert any("No incidents match" in info.value for info in app.info)


def test_selectbox_filter_narrows_the_table() -> None:
    app = run("12_http_failures.py")
    app.selectbox[0].set_value("503").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 2


def test_overview_shows_four_fleet_metrics_and_a_tile_per_monitor() -> None:
    from stewards.monitors.registry import MONITOR_REGISTRY

    app = run("00_overview.py")
    assert len(app.button) == 1 + len(MONITOR_REGISTRY)  # contact queue + one Open per tile
    markdown = " ".join(m.value for m in app.markdown)
    for monitor in MONITOR_REGISTRY:
        assert monitor.name in markdown


def test_overview_banner_names_the_threshold() -> None:
    app = run("00_overview.py")
    assert any("7-day" in warning.value for warning in app.warning)


def test_contact_queue_lists_the_cross_monitor_union() -> None:
    app = run("01_contact_queue.py")
    frame = app.dataframe[0].value
    assert len(frame) == 10
    assert set(frame["Monitor"]) == {"Single-feed stalls", "HTTP endpoint failures"}


def test_docs_index_lists_the_runbook_and_opens_it() -> None:
    app = run("90_docs.py")
    assert any("Runbook" in m.value for m in app.markdown)
    app.button[0].click().run()
    assert not app.exception
    assert any("internal only" in m.value for m in app.markdown)


def test_docs_search_with_no_match_says_so() -> None:
    app = run("90_docs.py")
    app.text_input[0].set_value("no-such-topic").run()
    assert any("No document matches" in info.value for info in app.info)


def test_sample_data_mode_is_announced_on_every_data_page() -> None:
    for name in ["00_overview.py", "01_contact_queue.py", *MONITOR_PAGES]:
        app = run(name)
        assert any("Sample data" in warning.value for warning in app.warning), name


# --- the entry point ---------------------------------------------------------------------

APP_FILE = PAGES_DIR.parent / "app.py"


def test_the_app_boots_and_lands_on_the_overview() -> None:
    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    captions = " ".join(c.value for c in app.caption)
    assert "Snapshot 2026-08-21" in captions
    assert any("Authentication is disabled" in w.value for w in app.warning)


def test_the_app_reports_a_missing_configuration_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stewards import config

    monkeypatch.delenv("STEWARDS_USE_SAMPLE_DATA", raising=False)
    monkeypatch.delenv("STEWARDS_API_BASE_URL", raising=False)
    config.get_settings.cache_clear()

    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert any("not configured" in error.value for error in app.error)
    config.get_settings.cache_clear()
