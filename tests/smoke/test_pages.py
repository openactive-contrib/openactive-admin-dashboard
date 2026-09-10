"""AppTest renders of every page. Cheap insurance against a broken import.

These also cover the one thing a unit test cannot: that a pandas Styler, `column_config`
and `LineChartColumn` survive `st.dataframe` together.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

VIEWS_DIR = Path(__file__).resolve().parents[2] / "src" / "stewards" / "views"

PAGES = [
    "00_overview.py",
    "01_contact_queue.py",
    "10_single_feed_stalls.py",
    "12_feed_ingestion_errors.py",
    "22_dataset_orphaned_children.py",
]

MONITOR_PAGES = [
    "10_single_feed_stalls.py",
    "12_feed_ingestion_errors.py",
    "22_dataset_orphaned_children.py",
]

#: Page filename -> registry id, so the counts a page must render are read from the monitor
#: rather than hard-coded per page.
MONITOR_IDS = {
    "10_single_feed_stalls.py": "single_feed_stall",
    "12_feed_ingestion_errors.py": "feed_ingestion_error",
    "22_dataset_orphaned_children.py": "dataset_orphaned_children",
}


def run(name: str) -> AppTest:
    app = AppTest.from_file(str(VIEWS_DIR / name), default_timeout=30)
    app.run()
    return app


def text_of(app: AppTest) -> str:
    """All rendered text, whichever element carries it.

    Backticks are markdown syntax rather than content, and the injected stylesheet is not
    text at all, so both are stripped before matching.
    """
    parts = [
        *(m.value for m in app.markdown if not m.value.lstrip().startswith("<style>")),
        *(c.value for c in app.caption),
        *(b.label for b in app.button),
    ]
    return " ".join(parts).replace("`", "")


@pytest.mark.parametrize("name", PAGES)
def test_page_renders_without_exception(name: str) -> None:
    app = run(name)
    assert not app.exception, [e.value for e in app.exception]


@pytest.mark.parametrize("name", PAGES)
def test_every_page_states_the_snapshot(name: str) -> None:
    """Hard rule: no data page may read as live."""
    text = text_of(run(name))
    assert "Snapshot 2026-08-21" in text
    assert "daily batch" in text


@pytest.mark.parametrize("name", MONITOR_PAGES)
def test_monitor_page_has_three_metrics_a_chart_and_one_table(name: str) -> None:
    from stewards.monitors.registry import get_monitor

    monitor = get_monitor(MONITOR_IDS[name])
    app = run(name)
    # One table: the row-detail tables render only once a row is selected.
    assert len(app.dataframe) == 1
    assert len(app.toggle) == (1 if monitor.has_threshold_filter else 0)
    assert len(app.selectbox) == len(monitor.filters)
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
    app = run("12_feed_ingestion_errors.py")
    app.text_input[0].set_value("halo").run()
    assert len(app.dataframe[0].value) == 1
    assert not app.exception


def test_search_with_no_match_renders_an_empty_state_not_an_error() -> None:
    app = run("12_feed_ingestion_errors.py")
    app.text_input[0].set_value("no-such-publisher").run()
    assert not app.exception
    assert not app.dataframe
    assert any("No incidents match" in info.value for info in app.info)


def test_selectbox_filter_narrows_the_table() -> None:
    app = run("12_feed_ingestion_errors.py")
    app.selectbox[0].set_value("503").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 2


def test_overview_shows_four_fleet_metrics_and_a_tile_per_monitor() -> None:
    from stewards.monitors.registry import MONITOR_REGISTRY

    app = run("00_overview.py")
    assert len(app.button) == 1  # only the contact-queue call to action; tiles use page links
    text = text_of(app)
    for monitor in MONITOR_REGISTRY:
        assert monitor.name in text
        assert monitor.unit in text


def test_each_tile_carries_a_state_chip_and_a_sparkline() -> None:
    from stewards.monitors.registry import MONITOR_REGISTRY

    app = run("00_overview.py")
    markdown = " ".join(m.value for m in app.markdown)
    assert markdown.count("-badge[") >= len(MONITOR_REGISTRY)
    assert "CRITICAL" in markdown
    # One sparkline per tile, drawn as an Altair chart.
    assert len(app.get("vega_lite_chart")) == len(MONITOR_REGISTRY)


def test_each_tile_says_which_way_its_series_is_moving() -> None:
    """A tile judged on a daily series states the movement; one judged on a benchmark says so.

    A monitor with no history has no movement to report, and inventing one is the whole
    thing `monitors.gauge` exists to avoid — so its card names the benchmark instead.
    """
    from stewards.monitors.registry import MONITOR_REGISTRY
    from stewards.monitors.tile_viz import Sparkline

    app = run("00_overview.py")
    captions = [caption.value for caption in app.caption]
    text = " ".join(captions) + " ".join(m.value for m in app.markdown)
    series_tiles = [m for m in MONITOR_REGISTRY if isinstance(m.viz, Sparkline)]
    assert sum("snapshots" in caption for caption in captions) == len(series_tiles)
    for monitor in MONITOR_REGISTRY:
        if not isinstance(monitor.viz, Sparkline):
            assert "benchmark" in text


def test_overview_banner_names_the_threshold() -> None:
    app = run("00_overview.py")
    assert any("7-day" in warning.value for warning in app.warning)


def test_contact_queue_lists_the_cross_monitor_union() -> None:
    app = run("01_contact_queue.py")
    frame = app.dataframe[0].value
    assert len(frame) == 10
    assert set(frame["Monitor"]) == {"Single-feed stalls", "Feed ingestion errors"}


# --- the entry point ---------------------------------------------------------------------

APP_FILE = VIEWS_DIR.parent / "app.py"


def test_the_app_boots_and_lands_on_the_overview() -> None:
    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    assert "Snapshot 2026-08-21" in text_of(app)
    assert any("Authentication is disabled" in w.value for w in app.warning)


def test_the_login_screen_is_reached_with_the_card_stylesheet_already_on_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the gate stops the script, so the styles must be emitted above it.

    Injected below `require_login`, the sign-in and denied screens got no CSS at all — the
    white card fill and the card's type scale both silently vanished.
    """
    from stewards import config
    from stewards.components import theme

    monkeypatch.setenv("STEWARDS_DISABLE_AUTH", "false")
    config.get_settings.cache_clear()

    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert any("Continue with Google" in button.label for button in app.button)
    styles = [m.value for m in app.markdown if m.value.lstrip().startswith("<style>")]
    assert styles, "the login screen rendered with no stylesheet"
    assert f"background-color: {theme.SURFACE}" in styles[0]

    config.get_settings.cache_clear()


def test_the_app_reports_a_missing_configuration_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stewards import config

    monkeypatch.delenv("STEWARDS_API_BASE_URL", raising=False)
    config.get_settings.cache_clear()

    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert any("not configured" in error.value for error in app.error)
    config.get_settings.cache_clear()


def test_no_pages_directory_sits_beside_the_entrypoint() -> None:
    """Regression guard for an auth bypass.

    Streamlit sets `PagesManager.uses_pages_directory` when a folder literally named
    `pages` exists next to the entrypoint, which switches the app into v1 multipage mode.
    In that mode every page file becomes its own entrypoint, so a deep link like
    `/single_feed_stalls` runs the page script directly and never executes `app.py` — and
    therefore never runs the auth gate. The page modules live in `views/` for this reason.
    """
    assert not (APP_FILE.parent / "pages").exists()
    assert (APP_FILE.parent / "views").is_dir()


def test_every_registered_monitor_page_lives_under_views() -> None:
    from stewards.monitors.registry import MONITOR_REGISTRY

    for monitor in MONITOR_REGISTRY:
        assert monitor.page.startswith("views/")
        assert (APP_FILE.parent / monitor.page).is_file()


def test_the_header_shows_a_delta_when_the_api_supplies_one() -> None:
    text = text_of(run("00_overview.py"))
    assert "+3" in text  # publishers with issues
    assert "+5" in text  # open incidents
    assert "+2" in text  # past threshold
