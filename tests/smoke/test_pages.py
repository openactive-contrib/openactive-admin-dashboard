"""AppTest renders of every page. Cheap insurance against a broken import.

These also cover the one thing a unit test cannot: that a pandas Styler, `column_config`
and `LineChartColumn` survive `st.dataframe` together.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest
import respx
from streamlit.testing.v1 import AppTest

from stewards.monitors.publisher_notifications import PUBLISHER_LINK_TEXT

VIEWS_DIR = Path(__file__).resolve().parents[2] / "src" / "stewards" / "views"

PAGES = [
    "00_overview.py",
    "01_publisher_notifications.py",
    "05_ecosystem_growth.py",
    "10_dataset_stalls.py",
    "11_single_feed_stalls.py",
    "12_feed_ingestion_errors.py",
    "22_dataset_orphaned_children.py",
    "23_dataset_future_decline.py",
    "30_feed_quality.py",
    "31_active_places_coverage.py",
    "33_schema_compliance.py",
    "33_activity_coverage.py",
    "90_notebooks.py",
]

#: Pages rendered by `components.monitor_page` — the incident shape. The quality page has a
#: shape of its own and is asserted separately below.
MONITOR_PAGES = [
    "10_dataset_stalls.py",
    "11_single_feed_stalls.py",
    "12_feed_ingestion_errors.py",
    "22_dataset_orphaned_children.py",
    "23_dataset_future_decline.py",
]

QUALITY_PAGES = ["30_feed_quality.py"]

COVERAGE_PAGES = ["31_active_places_coverage.py"]


SCHEMA_COMPLIANCE_PAGES = ["33_schema_compliance.py"]

#: Page filename -> registry id, so the counts a page must render are read from the monitor
#: rather than hard-coded per page.
MONITOR_IDS = {
    "10_dataset_stalls.py": "dataset_stall",
    "11_single_feed_stalls.py": "single_feed_stall",
    "12_feed_ingestion_errors.py": "feed_ingestion_error",
    "22_dataset_orphaned_children.py": "dataset_orphaned_children",
    "23_dataset_future_decline.py": "dataset_future_decline",
    "30_feed_quality.py": "feed_quality",
    "31_active_places_coverage.py": "active_places_coverage",
    "33_schema_compliance.py": "schema_compliance",
    "33_activity_coverage.py": "activity_coverage",
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


#: Every page over the daily batch. The notification list is stored rows, not a batch
#: snapshot, and states its own source; the notebooks page reads no data at all. Both have
#: their own tests further down. The growth page's fixture is the live API's own payload,
#: dated 2026-11-13, so its snapshot is asserted with the rest of that page below.
BATCH_PAGES = [
    name
    for name in PAGES
    if name
    not in {"01_publisher_notifications.py", "05_ecosystem_growth.py", "90_notebooks.py"}
]


@pytest.mark.parametrize("name", BATCH_PAGES)
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

    app = run("11_single_feed_stalls.py")
    frame = app.dataframe[0].value
    assert list(frame.columns) == [c.label for c in get_monitor("single_feed_stall").columns]
    assert len(frame) == 23


def test_threshold_toggle_narrows_the_table() -> None:
    app = run("11_single_feed_stalls.py")
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


@pytest.mark.parametrize("name", QUALITY_PAGES)
def test_quality_page_has_five_figures_four_charts_and_one_table(name: str) -> None:
    from stewards.monitors.registry import get_monitor

    monitor = get_monitor(MONITOR_IDS[name])
    app = run(name)
    assert len(app.dataframe) == 1
    assert len(app.get("vega_lite_chart")) == 4
    assert len(app.selectbox) == len(monitor.filters)
    assert len(app.toggle) == 1  # "issues only" stands where the threshold toggle would
    assert len(app.text_input) == 1


def test_quality_table_carries_every_declared_column_grouped_by_dataset() -> None:
    from stewards.monitors.registry import get_monitor

    app = run("30_feed_quality.py")
    frame = app.dataframe[0].value
    assert list(frame.columns) == [c.label for c in get_monitor("feed_quality").columns]
    assert len(frame) == 15
    # A dataset's feeds are consecutive: no dataset name reappears after another intervenes.
    datasets = list(frame["Dataset"])
    assert len(list(dict.fromkeys(datasets))) == len({*datasets})


def test_quality_issues_toggle_narrows_the_table() -> None:
    app = run("30_feed_quality.py")
    assert len(app.dataframe[0].value) == 15
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 5


def run_with_pairs(name: str) -> AppTest:
    """A coverage page with its site-venue pairs asked for: the table loads on request."""
    app = run(name)
    app.button(key=f"load_pairs_{MONITOR_IDS[name]}").click().run()
    return app


@pytest.mark.parametrize("name", COVERAGE_PAGES)
def test_coverage_page_opens_with_figures_two_charts_and_no_table(
    name: str, smoke_env: respx.MockRouter
) -> None:
    """The pairs are ten thousand rows; the first render must not wait for them."""
    app = run(name)
    assert not app.exception
    # The region chart and the map's legend; the map itself is deck.gl.
    assert len(app.get("vega_lite_chart")) == 2
    assert len(app.get("deck_gl_json_chart")) == 1
    assert len(app.dataframe) == 0
    # The map's own "centre on" list is the only selectbox until the pairs load.
    assert [s.key for s in app.selectbox] == [f"chart_{MONITOR_IDS[name]}_la_focus"]
    assert [b.label for b in app.button if b.label] == ["Load site-venue pairs"]
    assert not smoke_env[f"{MONITOR_IDS[name]}_mappings"].called
    assert "Coverage by local authority" in [h.value for h in app.subheader]


@pytest.mark.parametrize("name", COVERAGE_PAGES)
def test_coverage_page_loads_the_table_on_request(name: str) -> None:
    from stewards.monitors.registry import get_monitor

    monitor = get_monitor(MONITOR_IDS[name])
    app = run_with_pairs(name)
    assert not app.exception
    assert len(app.dataframe) == 1
    assert len(app.get("vega_lite_chart")) == 2
    filters = [s for s in app.selectbox if s.key != f"chart_{monitor.id}_la_focus"]
    assert len(filters) == len(monitor.filters)
    # "primary pairs only" stands where the threshold toggle would
    assert len(app.toggle) == 1
    assert len(app.text_input) == 1
    # The button has done its job and is gone.
    assert not any(b.label == "Load site-venue pairs" for b in app.button)


def test_coverage_table_carries_every_declared_column() -> None:
    from fixture_loader import load_sample
    from stewards.monitors.registry import get_monitor

    app = run_with_pairs("31_active_places_coverage.py")
    frame = app.dataframe[0].value
    monitor = get_monitor("active_places_coverage")
    assert list(frame.columns) == [c.label for c in monitor.columns]
    assert len(frame) == len(load_sample("active_places_coverage_mappings")["data"])


def test_coverage_primary_pairs_toggle_narrows_the_table() -> None:
    from fixture_loader import load_sample

    rows = load_sample("active_places_coverage_mappings")["data"]
    primary = sum(1 for row in rows if row["is_primary_for_venue"])
    app = run_with_pairs("31_active_places_coverage.py")
    assert len(app.dataframe[0].value) == len(rows)
    # A filter rerun keeps the table: the request outlives the click that made it.
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert len(app.dataframe[0].value) == primary
    assert primary < len(rows)


def test_the_coverage_page_states_the_run_figures_not_the_filtered_ones() -> None:
    """The four figures describe the whole estate; the table describes the pairs shown."""
    app = run_with_pairs("31_active_places_coverage.py")
    text = text_of(app)
    assert "26.4%" in text
    assert "7,351" in text
    assert "13 of 13 site-venue pairs shown" in text
    assert "Actions available: none" in text
    # The run's own pair count was dropped as a figure: the caption above the table already
    # says how many rows there are, and the two disagreeing read as a bug.
    assert "10,034" not in text


ACTIVITY_PAGE = "33_activity_coverage.py"
ACTIVITY_FILTER = "filter_activity_coverage_activity"
ACTIVITY_FOCUS = "chart_activity_coverage_districts_focus"


def table_caption(app: AppTest) -> str:
    return next(c.value for c in app.caption if "pairs shown" in c.value)


def test_activity_page_has_three_figures_two_charts_a_map_and_one_table() -> None:
    from fixture_loader import load_sample

    app = run(ACTIVITY_PAGE)
    assert not app.exception
    # The ranking and the map's legend; the map itself is deck.gl.
    assert len(app.get("vega_lite_chart")) == 2
    assert len(app.get("deck_gl_json_chart")) == 1
    assert [s.key for s in app.selectbox] == [ACTIVITY_FOCUS, ACTIVITY_FILTER]
    assert app.selectbox(key=ACTIVITY_FILTER).value == "All activities"
    assert [t.key for t in app.text_input] == ["search_activity_coverage"]
    assert len(app.dataframe) == 1
    assert len(app.dataframe[0].value) == len(
        load_sample("activity_coverage_districts")["data"]
    )
    text = text_of(app)
    for figure in ("683,255", "447,497", "288", "65.5%", "of 768 in the activity list"):
        assert figure in text
    assert "Top 20 activities" in [h.value for h in app.subheader]
    assert "Actions available: none" in text
    # Across all activities the map counts tags, and says so.
    assert "counts once under each" in text


def test_activity_table_carries_every_declared_column() -> None:
    from stewards.monitors.registry import get_monitor

    app = run(ACTIVITY_PAGE)
    frame = app.dataframe[0].value
    assert list(frame.columns) == [c.label for c in get_monitor("activity_coverage").columns]


def test_the_activity_filter_narrows_the_map_and_the_table() -> None:
    from fixture_loader import load_sample

    rows = load_sample("activity_coverage_districts")["data"]
    swimming = sum(1 for row in rows if row["activity"] == "Swimming")
    app = run(ACTIVITY_PAGE)
    app.selectbox(key=ACTIVITY_FILTER).set_value("Swimming").run()
    assert not app.exception
    frame = app.dataframe[0].value
    assert len(frame) == swimming < len(rows)
    assert set(frame["Activity"]) == {"Swimming"}
    # One activity is an opportunity count, so the tag caveat goes.
    assert "counts once under each" not in " ".join(c.value for c in app.caption)


def test_the_local_authority_filter_narrows_the_table_with_the_activity() -> None:
    app = run(ACTIVITY_PAGE)
    app.selectbox(key=ACTIVITY_FOCUS).set_value("E08000025").run()
    assert not app.exception
    assert set(app.dataframe[0].value["Local authority"]) == {"Birmingham"}
    assert "Narrowed to Birmingham" in table_caption(app)
    app.selectbox(key=ACTIVITY_FILTER).set_value("Swimming").run()
    frame = app.dataframe[0].value
    assert list(zip(frame["Local authority"], frame["Activity"], strict=True)) == [
        ("Birmingham", "Swimming")
    ]


def test_the_search_narrows_the_table_by_local_authority_name() -> None:
    from fixture_loader import load_sample

    rows = load_sample("activity_coverage_districts")["data"]
    cardiff = sum(1 for row in rows if row["district_name"] == "Cardiff")
    app = run(ACTIVITY_PAGE)
    app.text_input(key="search_activity_coverage").input("cardiff").run()
    assert not app.exception
    frame = app.dataframe[0].value
    assert set(frame["Local authority"]) == {"Cardiff"}
    assert table_caption(app).startswith(f"{cardiff} of {len(rows)} ")
    # The search narrows the table only; the map still draws.
    assert len(app.get("deck_gl_json_chart")) == 1


def test_a_search_with_no_match_renders_an_empty_state_not_an_error() -> None:
    app = run(ACTIVITY_PAGE)
    app.text_input(key="search_activity_coverage").input("Atlantis").run()
    assert not app.exception
    assert not app.dataframe
    assert any("Clear the search" in i.value for i in app.info)


def test_showing_the_whole_map_clears_the_local_authority() -> None:
    from fixture_loader import load_sample

    total = len(load_sample("activity_coverage_districts")["data"])
    app = run(ACTIVITY_PAGE)
    app.selectbox(key=ACTIVITY_FOCUS).set_value("E08000025").run()
    app.button(key="chart_activity_coverage_districts_whole").click().run()
    assert not app.exception
    assert len(app.dataframe[0].value) == total


def test_an_activity_and_district_with_nothing_shows_an_empty_state_not_an_error() -> None:
    app = run(ACTIVITY_PAGE)
    app.selectbox(key=ACTIVITY_FOCUS).set_value("E06000057").run()  # Northumberland
    app.selectbox(key=ACTIVITY_FILTER).set_value("Football").run()
    assert not app.exception
    assert not app.dataframe
    assert any("No activity is reported" in i.value for i in app.info)
    assert table_caption(app).startswith("0 of 20 ")


@pytest.mark.parametrize("name", SCHEMA_COMPLIANCE_PAGES)
def test_schema_compliance_page_has_two_tabs_each_with_a_chart_and_a_table(name: str) -> None:
    """`AppTest` renders both tabs, so the counts are the two of them together: the feeds
    table and the key field chart, then the fleet's properties and the chart above them.

    The selected feed's key fields and the feeds using a selected property render only once
    a row is selected, so neither is here.
    """
    from stewards.monitors.registry import get_monitor

    monitor = get_monitor(MONITOR_IDS[name])
    app = run(name)
    assert [frame.key for frame in app.dataframe] == [
        f"table_{monitor.id}",
        f"properties_{monitor.id}",
    ]
    assert len(app.tabs) == 2
    assert len(app.get("vega_lite_chart")) == 2
    # The filters belong to the Feeds tab; the Properties tab narrows nothing.
    assert len(app.selectbox) == len(monitor.filters)
    # "not passing only" stands where the threshold toggle would
    assert len(app.toggle) == 1
    assert len(app.text_input) == 1


def test_schema_compliance_table_carries_every_declared_column() -> None:
    """The join is driven by the quality read, so the table holds its feeds and no others."""
    from fixture_loader import load_sample
    from stewards.monitors.registry import get_monitor

    app = run("33_schema_compliance.py")
    frame = app.dataframe[0].value
    monitor = get_monitor("schema_compliance")
    assert list(frame.columns) == [c.label for c in monitor.columns]
    assert len(frame) == len(load_sample("feed_quality_quality")["data"])


def test_schema_compliance_verdict_filter_narrows_the_table_to_the_failing_feed() -> None:
    app = run("33_schema_compliance.py")
    assert len(app.dataframe[0].value) == 15
    app.selectbox[0].set_value("fail").run()
    assert not app.exception
    frame = app.dataframe[0].value
    assert list(frame["Compliance"]) == ["Fail"]
    # The count and the field it counts, side by side: the review asked for both.
    assert list(frame["Missing required"]) == [1]
    assert list(frame["Missing fields"]) == ["activity"]


def test_the_compliance_page_states_the_fleet_figures_not_the_filtered_ones() -> None:
    """The five figures describe every assessed feed; the caption describes the rows shown."""
    app = run("33_schema_compliance.py")
    app.text_input[0].set_value("better").run()
    markdown = [m.value for m in app.markdown]
    text = text_of(app)
    assert "3 of 15 feeds shown" in text
    assert markdown[markdown.index("FEEDS CHECKED") + 1] == "15"
    assert markdown[markdown.index("FAILING") + 1] == ":red[1]"
    assert markdown[markdown.index("WARNINGS") + 1] == ":orange[14]"
    assert "Actions available: none" in text


def test_the_properties_tab_states_the_fleet_figures_and_lists_every_property() -> None:
    """The whole of what the retired schema drift page showed, on the same payload: the
    fleet figures off the summary block, and one line per custom property."""
    from fixture_loader import load_sample
    from stewards.monitors import schema_drift

    payload = load_sample("feed_custom_properties_properties")
    app = run("33_schema_compliance.py")
    frame = app.dataframe[1].value
    assert list(frame.columns) == [col.label for col in schema_drift.PROPERTY_COLUMNS]
    assert len(frame) == len(payload["summary"]["property_breakdown"])
    text = text_of(app)
    assert "62" in text  # datasets with custom properties, fleet-wide
    assert "of 159 datasets assessed" in text
    assert "40.6% of 397 feeds assessed" in text
    assert "custom properties, most widely used first" in text


def test_the_join_carries_custom_property_counts_onto_the_quality_feeds() -> None:
    """A feed in both snapshots gets its count; a feed in only the quality one gets a real
    zero. A blank is reserved for the custom-property read having failed."""
    app = run("33_schema_compliance.py")
    counts = list(app.dataframe[0].value["Custom properties"])
    assert sorted(count for count in counts if count) == [1, 6, 18]
    assert counts.count(0) == 12
    text = text_of(app)
    assert "20% of feeds checked" in text
    # The counts are real, so the page must not be carrying the read-failed caveat.
    from stewards.components.schema_compliance_page import PROPERTIES_UNAVAILABLE

    assert PROPERTIES_UNAVAILABLE not in text


def test_overview_shows_four_fleet_metrics_and_a_tile_per_monitor() -> None:
    from stewards.monitors.registry import MONITOR_REGISTRY

    app = run("00_overview.py")
    assert not app.button  # tiles use page links
    text = text_of(app)
    for monitor in MONITOR_REGISTRY:
        assert monitor.name in text
        assert monitor.unit in text


def test_each_tile_carries_a_state_chip_and_a_sparkline() -> None:
    from stewards.monitors.registry import MONITOR_REGISTRY
    from stewards.monitors.tile_viz import Facts

    app = run("00_overview.py")
    markdown = " ".join(m.value for m in app.markdown)
    assert markdown.count("-badge[") >= len(MONITOR_REGISTRY)
    assert "CRITICAL" in markdown
    # One chart per tile — except a card whose figures are its visualisation, which draws
    # none at all rather than an empty axis.
    charted = [m for m in MONITOR_REGISTRY if not isinstance(m.viz, Facts)]
    assert len(app.get("vega_lite_chart")) == len(charted)


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
    assert "benchmark" in text  # the gauge card names its reference instead of a movement


def test_the_compliance_tile_states_the_join_rather_than_not_reported() -> None:
    """/summary describes neither read behind this monitor, so the card is handed over
    whole — without it the tile would read "not reported"."""
    text = text_of(run("00_overview.py"))
    assert "of 15 feeds assessed · this snapshot only" in text
    assert "feeds failing" in text
    for label in ("Feeds checked", "Warnings", "Key fields"):
        assert label in text


def test_overview_has_no_threshold_banner() -> None:
    app = run("00_overview.py")
    assert not app.warning
    assert not app.info


def test_overview_kpis_match_the_notification_list() -> None:
    """Open incidents and past contact threshold are the list's "open" and "need attention"."""
    markdown = [m.value for m in run("00_overview.py").markdown]
    # The summary fixture says open 2, alerts 2; /summary's own figures differ.
    open_at = markdown.index("OPEN INCIDENTS")
    past_at = markdown.index("PAST CONTACT THRESHOLD")
    assert markdown[open_at + 1 : open_at + 3] == [
        ":red[2]",
        "open rows on the notification list",
    ]
    assert markdown[past_at + 1 : past_at + 3] == [
        ":red[2]",
        "notification list rows needing attention",
    ]


def test_notification_list_hides_closed_rows_by_default() -> None:
    app = run("01_publisher_notifications.py")
    assert not app.exception, [e.value for e in app.exception]
    frame = app.dataframe[0].value
    shown = {
        unquote(re.fullmatch(PUBLISHER_LINK_TEXT, cell).group(1))  # type: ignore[union-attr]
        for cell in frame["Publisher"]
    }
    assert shown == {"Dummy Leisure", "Dummy Sports Trust"}
    assert "https://dummy-leisure.example.org/openactive#Dummy%20Leisure" in set(
        frame["Publisher"]
    )
    text = text_of(app)
    assert "Snapshot 2026-10-13" in text
    assert "Stored work list · updated as you save" in text
    assert "BigQuery · daily batch" not in text


def test_notification_list_shows_closed_rows_on_request() -> None:
    app = run("01_publisher_notifications.py")
    app.toggle(key="pn_show_closed").set_value(True).run()
    assert len(app.dataframe[0].value) == 3


def test_the_notebooks_page_links_each_notebook_to_colab() -> None:
    from stewards.notebooks import load_manifest

    manifest = load_manifest()
    app = run("90_notebooks.py")
    text = text_of(app)
    assert "Snapshot" not in text  # no data behind it, so no snapshot to claim
    assert "notebooks/notebooks.yaml" in text
    assert manifest.readme_url() in text
    buttons = [element.proto for element in app.get("link_button")]
    # Icon-only buttons: the tooltip carries what the label would have said.
    assert {(b.label, b.help) for b in buttons} == {
        ("", "Open in Colab"),
        ("", "View on GitHub"),
    }
    links = [b.url for b in buttons]
    for notebook in manifest.notebooks:
        assert notebook.title in text
        assert manifest.colab_url(notebook) in links
        assert manifest.github_url(notebook) in links


def test_the_sidebar_lists_the_notebooks_page_under_reference() -> None:
    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    sidebar = [element.proto for element in app.sidebar.get("page_link")]
    labels = [link.label for link in sidebar]
    assert labels[-2:] == ["Notebooks", "Documentation"]
    assert sidebar[-2].page.endswith("notebooks")


def test_the_sidebar_shows_the_notification_alert_count() -> None:
    app = AppTest.from_file(str(APP_FILE), default_timeout=60)
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    # The summary fixture reports 2 alerts.
    markdown = " ".join(m.value for m in app.markdown)
    assert ":red-badge[2]" in markdown


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


def test_the_overview_has_no_publishers_with_issues_kpi_and_no_deltas() -> None:
    """Removed on request; /summary's deltas belonged to figures the KPIs no longer show."""
    markdown = [m.value for m in run("00_overview.py").markdown]
    assert "PUBLISHERS WITH ISSUES" not in markdown
    assert "OPEN INCIDENTS" in markdown
    for delta in ("+3", "+5", "+2"):
        assert delta not in markdown


# --- ecosystem growth -----------------------------------------------------------------------

GROWTH_PAGE = "05_ecosystem_growth.py"


def test_the_growth_page_states_its_snapshot() -> None:
    text = text_of(run(GROWTH_PAGE))
    assert "ECOSYSTEM" in text
    assert "Growth" in text
    assert "Snapshot 2026-11-13" in text
    assert "daily batch" in text
    assert "Actions available: none" in text


def test_the_growth_page_charts_the_default_series_and_tables_every_month() -> None:
    from stewards.monitors.ecosystem import DEFAULT_SERIES, TABLE_COLUMNS

    app = run(GROWTH_PAGE)
    assert len(app.get("vega_lite_chart")) == 1
    assert app.multiselect[0].value == list(DEFAULT_SERIES)
    frame = app.dataframe[0].value
    assert list(frame.columns) == [col.label for col in TABLE_COLUMNS]
    assert list(frame["Month"]) == [
        "2026-11",
        "2026-10",
        "2026-09",
        "2026-08",
        "2026-07",
        "2026-06",
        "2026-05",
    ]
    text = text_of(app)
    assert "7 months, most recent first." in text
    assert "Select a month to see the datasets behind its figures." in text
    assert not app.tabs  # no month is open until one is selected


def test_the_growth_chart_follows_the_series_picker() -> None:
    app = run(GROWTH_PAGE)
    app.multiselect[0].set_value(["datasets_added", "datasets_removed"]).run()
    assert not app.exception
    assert len(app.get("vega_lite_chart")) == 1

    app.multiselect[0].set_value([]).run()
    assert not app.exception
    assert not app.get("vega_lite_chart")
    assert "Choose at least one series" in text_of(app)
    assert len(app.dataframe) == 1  # the table does not depend on the picker


def test_the_overview_has_no_growth_card() -> None:
    text = text_of(run("00_overview.py"))
    assert "Growth" not in text
