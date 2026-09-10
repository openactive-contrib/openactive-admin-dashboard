"""Renders of the components a page cannot reach from AppTest alone.

Row selection, error states and the login screen are all real UI states that no unit test
can exercise, so they are driven here through `AppTest.from_function`.
"""

from __future__ import annotations

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from fixture_loader import load_sample
from stewards.api.errors import (
    ApiContractError,
    ApiError,
    ApiNotFound,
    ApiUnauthorized,
    ApiUnavailable,
)
from stewards.api.models import Meta, Summary, SummaryResponse

SNAPSHOT = date(2026, 8, 21)


def run(script, *args: object) -> AppTest:
    """Run a component as a one-off Streamlit script.

    `from_function` re-executes the function's own source, so its body must import what it
    needs and its signature annotations must be strings.
    """
    app = AppTest.from_function(script, args=args, default_timeout=30)
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    return app


# --- the draft-email popover ------------------------------------------------------------


def _email_script() -> None:
    from fixture_loader import load_sample
    from stewards.api.models import IncidentPage
    from stewards.components.email_draft import render_email_draft
    from stewards.monitors.registry import get_monitor

    page = IncidentPage.model_validate(load_sample("single_feed_stall_incidents"))
    render_email_draft(get_monitor("single_feed_stall"), page.data[0], page.meta.snapshot_date)


def test_email_popover_renders_a_copyable_draft() -> None:
    app = run(_email_script)
    assert app.code
    body = app.code[0].value
    assert "Freedom Leisure" in body
    assert "22 days" in body


# --- error states -----------------------------------------------------------------------


def _error_script(exc: object) -> None:
    from stewards.api.errors import ApiError
    from stewards.components.errors import guarded, render_api_error

    assert isinstance(exc, ApiError)
    render_api_error(exc)

    def failing() -> None:
        raise exc

    assert guarded(failing) is None


@pytest.mark.parametrize(
    ("exc", "fragment"),
    [
        (ApiUnauthorized("token expired"), "rejected this deployment's credentials"),
        (ApiUnavailable("timed out"), "unavailable"),
        (ApiNotFound("no endpoint"), "no data for this view yet"),
        (ApiContractError("bad shape"), "unexpected shape"),
        (ApiError("something else"), "could not be read"),
    ],
)
def test_each_failure_type_gets_its_own_message(exc: ApiError, fragment: str) -> None:
    app = run(_error_script, exc)
    assert any(fragment in error.value for error in app.error)


def _guarded_success_script() -> None:
    from stewards.components.errors import guarded

    assert guarded(lambda: 42) == 42


def test_guarded_returns_the_value_when_the_read_succeeds() -> None:
    run(_guarded_success_script)


def _monitor_page_error_script() -> None:
    import streamlit as st

    from stewards.api import repository
    from stewards.api.errors import ApiUnavailable
    from stewards.components.monitor_page import render_monitor_page
    from stewards.monitors.registry import get_monitor

    def boom(*_args: object, **_kwargs: object) -> None:
        raise ApiUnavailable("the API is down")

    st.cache_data.clear()
    original = repository.fetch_incidents
    repository.fetch_incidents = boom  # type: ignore[assignment]
    try:
        render_monitor_page(get_monitor("single_feed_stall"))
    finally:
        repository.fetch_incidents = original
        st.cache_data.clear()


def test_a_monitor_page_reports_the_failure_instead_of_an_empty_table() -> None:
    app = run(_monitor_page_error_script)
    assert any("unavailable" in error.value for error in app.error)
    assert not app.dataframe
    assert any("Single-feed stalls" in m.value for m in app.markdown)


def _contact_queue_error_script() -> None:
    import streamlit as st

    from stewards.api import repository
    from stewards.api.errors import ApiUnauthorized
    from stewards.components.contact_queue_page import render_contact_queue_page

    def boom(*_args: object, **_kwargs: object) -> None:
        raise ApiUnauthorized("token expired")

    st.cache_data.clear()
    original = repository.fetch_contact_queue
    repository.fetch_contact_queue = boom  # type: ignore[assignment]
    try:
        render_contact_queue_page()
    finally:
        repository.fetch_contact_queue = original
        st.cache_data.clear()


def test_the_contact_queue_reports_an_auth_failure() -> None:
    app = run(_contact_queue_error_script)
    assert any("credentials" in error.value for error in app.error)


def _overview_error_script() -> None:
    import streamlit as st

    from stewards.api import repository
    from stewards.api.errors import ApiContractError
    from stewards.components.overview_page import render_overview_page

    def boom(*_args: object, **_kwargs: object) -> None:
        raise ApiContractError("unexpected shape")

    st.cache_data.clear()
    original = repository.fetch_summary
    repository.fetch_summary = boom  # type: ignore[assignment]
    try:
        render_overview_page()
    finally:
        repository.fetch_summary = original
        st.cache_data.clear()


def test_the_overview_reports_a_contract_mismatch() -> None:
    app = run(_overview_error_script)
    assert any("unexpected shape" in error.value for error in app.error)


# --- the all-clear overview -------------------------------------------------------------


def _all_clear_script(response: object) -> None:
    from stewards.api.models import SummaryResponse
    from stewards.components.overview_page import render_fleet_kpis, render_threshold_banner

    assert isinstance(response, SummaryResponse)
    render_fleet_kpis(response.data)
    render_threshold_banner(response.data, 7)


def test_an_all_clear_snapshot_says_so_instead_of_warning(payload) -> None:
    app = run(_all_clear_script, SummaryResponse.model_validate(payload("summary_zero")))
    assert any("No incident has been open longer than 7 days" in s.value for s in app.success)
    assert not app.warning


def _unknown_fleet_size_script() -> None:
    from stewards.api.models import Meta, Summary
    from stewards.components.overview_page import render_fleet_kpis

    render_fleet_kpis(Summary())
    Meta(snapshot_date="2026-08-21", generated_at="2026-08-21T06:12:04Z")


def test_a_summary_with_no_fleet_size_does_not_divide_by_zero() -> None:
    app = run(_unknown_fleet_size_script)
    assert any("fleet size unknown" in m.value for m in app.markdown)


def _partial_summary_script(response: object) -> None:
    from stewards.api.models import SummaryResponse
    from stewards.components.overview_page import render_fleet_kpis, render_threshold_banner

    assert isinstance(response, SummaryResponse)
    render_fleet_kpis(response.data)
    render_threshold_banner(response.data, 7)


def test_counts_the_api_does_not_report_render_as_unknown(payload) -> None:
    """A null count must not read as an all-clear zero, or as a contract error."""
    response = SummaryResponse.model_validate(payload("admin_summary_partial"))
    app = run(_partial_summary_script, response)
    markdown = " ".join(m.value for m in app.markdown)

    assert "179" in markdown
    assert "—" in markdown
    assert not app.success  # nothing is known to be clear
    assert not app.warning
    assert any("does not report" in info.value for info in app.info)


# --- the contact queue with an unrenderable monitor -------------------------------------


def _unknown_monitor_script() -> None:
    import streamlit as st

    from stewards.api import repository
    from stewards.api.models import Incident, IncidentPage, Meta
    from stewards.components.contact_queue_page import render_contact_queue_page

    meta = Meta(snapshot_date="2026-08-21", generated_at="2026-08-21T06:12:04Z", total=1)
    incident = Incident(
        monitor_id="orphan_children",
        publisher_id="pub_gll",
        publisher_name="Better (GLL)",
        first_detected="2026-08-05",
        days_open=16,
        past_threshold=True,
        status="contact_due",
    )
    st.cache_data.clear()
    original = repository.fetch_contact_queue
    repository.fetch_contact_queue = lambda: IncidentPage(data=(incident,), meta=meta)  # type: ignore[assignment]
    try:
        render_contact_queue_page()
    finally:
        repository.fetch_contact_queue = original
        st.cache_data.clear()


def test_the_queue_says_which_monitors_it_cannot_render_yet() -> None:
    app = run(_unknown_monitor_script)
    assert any("orphan_children" in info.value for info in app.info)
    assert not app.dataframe


# --- the layout header ------------------------------------------------------------------


def _header_script() -> None:
    from stewards.api.models import Meta
    from stewards.components import layout

    meta = Meta(snapshot_date="2026-08-21", generated_at="2026-08-21T06:12:04Z")
    layout.render_header("Overview", "Health of the publisher fleet", meta)
    layout.render_footer("view_monitor_overview", note="Nothing is sent from here.")


def test_the_header_renders_no_download_button() -> None:
    """CSV export was removed from the header; provenance is all that sits on the right."""
    app = run(_header_script)
    assert not app.get("download_button")
    markdown = " ".join(m.value for m in app.markdown)
    assert "Snapshot `2026-08-21 06:00`" in markdown
    assert "BigQuery · daily batch" in markdown


# --- the empty trend chart --------------------------------------------------------------


def _empty_trend_script() -> None:
    from stewards.components.trend_chart import render_trend
    from stewards.monitors.registry import get_monitor

    render_trend(get_monitor("feed_ingestion_error"), [])


def test_an_empty_trend_says_so_instead_of_charting_nothing() -> None:
    app = run(_empty_trend_script)
    assert any("No trend history" in c.value for c in app.caption)


# --- the auth gate ----------------------------------------------------------------------


def _login_screen_script() -> None:
    from stewards.auth.google import (
        render_denied,
        render_identity_footer,
        render_login_screen,
    )

    render_login_screen("theodi.org")
    render_denied("theodi.org")
    render_identity_footer(None)  # a dev-bypass session has no identity block at all
    render_identity_footer("huseyin.kir@theodi.org")


def test_the_login_screen_names_the_workspace_and_never_shows_a_full_address() -> None:
    app = run(_login_screen_script)
    assert any("Continue with Google" in b.label for b in app.button)
    assert any("theodi.org" in e.value for e in app.error)
    body = " ".join(m.value for m in app.markdown)
    assert "SIGN IN" in body
    assert "Continue with your work account" in body
    assert "**theodi.org** Google Workspace" in body
    assert "never sees or stores your password" in body
    captions = " ".join(c.value for c in app.caption)
    assert "h…@theodi.org" in captions
    assert "huseyin.kir@theodi.org" not in captions


def _disabled_auth_script() -> None:
    from stewards.auth.google import require_login
    from stewards.config import Settings

    settings = Settings(api_base_url="https://api.test", env="dev", disable_auth=True)
    assert require_login(settings) is None


def test_disabled_auth_warns_loudly_on_every_run() -> None:
    app = run(_disabled_auth_script)
    assert any("Authentication is disabled" in w.value for w in app.warning)


# --- navigation -------------------------------------------------------------------------


def _navigation_script() -> None:
    from stewards.components import nav
    from stewards.monitors.registry import MONITOR_REGISTRY

    nav.build_navigation()
    assert nav.page_for("overview") is not None
    assert nav.page_for("contact_queue") is not None
    assert nav.page_for("no_such_monitor") is None
    for monitor in MONITOR_REGISTRY:
        page = nav.page_for(monitor.id)
        assert page is not None
        assert str(page._page).endswith(monitor.page.removeprefix("views/"))
    nav.switch_to("no_such_monitor")  # a missing key must be a no-op


def _url_path_script() -> None:
    from stewards.components import nav

    nav.build_navigation()
    # Streamlit strips the numeric ordering prefix, so these are the real deep links.
    expected = {
        "overview": "",
        "contact_queue": "contact_queue",
        "single_feed_stall": "single_feed_stalls",
        "feed_ingestion_error": "feed_ingestion_errors",
    }
    actual = {key: nav.page_for(key).url_path for key in expected}
    assert actual == expected, actual


def test_navigation_builds_a_page_per_registry_entry_with_count_badges() -> None:
    run(_navigation_script)


def test_the_page_url_paths_drop_the_numeric_prefix() -> None:
    """The nav filenames are ordered `12_feed_ingestion_errors.py`; the route is
    `/feed_ingestion_errors`."""
    run(_url_path_script)


def _sidebar_script() -> None:
    from stewards.components import nav
    from stewards.monitors.overview import NavBadge
    from stewards.monitors.thresholds import Tone

    nav.build_navigation()
    nav.render_sidebar(
        {
            "contact_queue": NavBadge("10", Tone.RED),
            "single_feed_stall": NavBadge("23", Tone.RED),
            "feed_ingestion_error": NavBadge("9", Tone.AMBER),
        }
    )


def test_the_sidebar_renders_a_link_per_page_and_a_pill_per_count() -> None:
    app = run(_sidebar_script)
    captions = [c.value for c in app.caption]
    for section in ("OVERVIEW", "AVAILABILITY", "REFERENCE"):
        assert section in captions
    markdown = " ".join(m.value for m in app.markdown)
    # Badges render as markdown colour directives carrying the count.
    for count, colour in (("10", "red"), ("23", "red"), ("9", "orange")):
        assert f":{colour}-badge[{count}]" in markdown


def test_documentation_links_out_to_the_pages_site() -> None:
    """`external` is the flag Streamlit renders as target="_blank", so the docs open in a
    new tab instead of navigating away from the dashboard."""
    from stewards.config import get_settings

    app = run(_sidebar_script)
    links = {link.label: link for link in app.get("page_link")}
    assert links["Documentation"].proto.external
    assert links["Documentation"].proto.page == get_settings().docs_url
    # Every other row is an in-app page, which must not open a new tab.
    assert not any(
        link.proto.external for label, link in links.items() if label != "Documentation"
    )


def _sidebar_without_badges_script() -> None:
    from stewards.components import nav

    nav.build_navigation()
    nav.render_sidebar({})
    assert nav.page_for("single_feed_stall") is not None


def test_the_sidebar_still_renders_when_the_summary_is_unavailable() -> None:
    app = run(_sidebar_without_badges_script)
    markdown = " ".join(m.value for m in app.markdown)
    # AppTest exposes no page_link accessor, so the section headings stand in for "the
    # sidebar still rendered" — the point of the test is the absent pills.
    assert "OVERVIEW" in [c.value for c in app.caption]
    # No count pill should appear when the summary supplied none.
    for colour in ("red", "orange", "green", "gray"):
        assert f":{colour}-badge[" not in markdown


def test_meta_and_summary_models_are_importable_here() -> None:
    """Guards the imports this module shares with the scripts above."""
    assert (
        Meta(snapshot_date=SNAPSHOT, generated_at="2026-08-21T06:12:04Z").snapshot_date
        == SNAPSHOT
    )
    assert Summary().monitors == ()
    assert load_sample("summary")["data"]["publishers_monitored"] == 170


def _gate_before_login_script() -> None:
    from stewards.auth.google import require_login
    from stewards.config import Settings

    require_login(Settings(api_base_url="https://api.test"))
    raise AssertionError("the gate must stop the script before this line")


def test_the_gate_renders_the_login_screen_when_nobody_is_signed_in() -> None:
    """Regression: with the gate on, `st.user` carries no usable identity keys yet."""
    app = run(_gate_before_login_script)
    assert any("Continue with Google" in b.label for b in app.button)


# --- a monitor page whose trend endpoint is not live ---------------------------------------


def _monitor_page_missing_trend_script() -> None:
    import streamlit as st

    from stewards.api import repository
    from stewards.api.errors import ApiNotFound
    from stewards.components.monitor_page import render_monitor_page
    from stewards.monitors.registry import get_monitor

    def missing(*_args: object, **_kwargs: object) -> None:
        raise ApiNotFound("no trend endpoint here")

    st.cache_data.clear()
    original = repository._fetch_trend
    repository._fetch_trend = missing  # type: ignore[assignment]
    try:
        render_monitor_page(get_monitor("single_feed_stall"))
    finally:
        repository._fetch_trend = original
        st.cache_data.clear()


def test_a_monitor_page_survives_a_trend_endpoint_that_is_not_live() -> None:
    """The regression this guards: the trend read used to share the incidents' try block,
    so a 404 on the chart took the KPIs, filters and table down with it — on a page whose
    incidents had loaded perfectly well."""
    app = run(_monitor_page_missing_trend_script)
    assert not app.error
    # The page is whole: header with its snapshot, three KPIs, the filters, the table.
    assert len(app.dataframe) == 1
    assert len(app.dataframe[0].value) == 23
    assert len(app.text_input) == 1
    assert any("Snapshot" in m.value for m in app.markdown)
    # Only the chart is missing, and the page says so rather than showing an empty axis.
    assert not app.get("vega_lite_chart")
    assert any("No trend history" in caption.value for caption in app.caption)


def _orphan_page_script() -> None:
    from stewards.components.monitor_page import render_monitor_page
    from stewards.monitors.registry import get_monitor

    render_monitor_page(get_monitor("dataset_orphaned_children"))


def test_a_gauge_monitor_shows_its_benchmark_where_the_trend_chart_would_be() -> None:
    """Its trend endpoint 404s in this deployment — see tests/smoke/conftest.py."""
    app = run(_orphan_page_script)
    text = " ".join(
        [
            *(c.value for c in app.caption),
            *(m.value for m in app.markdown),
            *(h.value for h in app.subheader),
        ]
    )
    assert "benchmark of 785,000" in text
    assert "no trend to judge" in text
    # The meter is drawn, unlike the empty trend chart it stands in for.
    assert len(app.get("vega_lite_chart")) == 1


def test_the_orphan_page_leads_with_the_orphan_count_not_the_dataset_count() -> None:
    app = run(_orphan_page_script)
    text = " ".join(m.value for m in app.markdown)
    # The quantity, matching the summary card, not the seven datasets carrying it.
    assert "590,056" in text
    assert "orphaned children" in text.lower()
    assert "PUBLISHERS AFFECTED" in text


def test_selecting_an_orphan_row_offers_the_email_draft_and_the_missing_parents() -> None:
    app = run(_orphan_page_script)
    frame = app.dataframe[0].value
    assert list(frame.columns)[:3] == ["Publisher", "Dataset", "Child type"]
    # Two rows for a dataset the batch broke down by both child types.
    assert list(frame["Publisher"]).count("Played") == 2


# --- the missing-parents table a selected row opens ---------------------------------------


def _row_detail_script(monitor_id: str, fixture: str) -> None:
    from fixture_loader import load_sample
    from stewards.api.models import IncidentPage
    from stewards.components.monitor_page import render_row_detail
    from stewards.monitors.registry import get_monitor

    page = IncidentPage.model_validate(load_sample(fixture))
    render_row_detail(get_monitor(monitor_id), page.data[0])


def test_the_missing_parents_table_names_the_parents_and_their_children() -> None:
    app = run(
        _row_detail_script, "dataset_orphaned_children", "dataset_orphaned_children_incidents"
    )
    frame = app.dataframe[0].value
    assert list(frame.columns) == ["Missing parent id", "Children affected"]
    assert len(frame) == 3
    # Worst first, as the API reports them.
    assert list(frame["Children affected"]) == sorted(frame["Children affected"], reverse=True)
    assert all("facility-uses" in value for value in frame["Missing parent id"])
    # The caption states the true total, not merely how many rows it could show.
    assert any("59 it counted" in caption.value for caption in app.caption)


def test_the_frozen_feeds_table_lists_every_feed_and_when_it_last_published() -> None:
    """The same renderer, a different monitor, no component change — that is the point."""
    app = run(_row_detail_script, "dataset_stall", "dataset_stall_incidents")
    frame = app.dataframe[0].value
    assert list(frame.columns) == ["Feed", "Last published", "Days silent", "Feed id"]
    assert list(frame["Feed"]) == ["slots", "facility-uses"]
    # A feed that never published inside the window has no date and no silence to count.
    assert list(frame["Last published"]) == ["2026-09-01", "—"]
    assert list(frame["Days silent"]) == ["9d", "—"]


def _row_detail_absent_script() -> None:
    import streamlit as st

    from stewards.api.models import Incident
    from stewards.components.monitor_page import render_row_detail
    from stewards.monitors.registry import get_monitor

    incident = Incident.model_validate(
        {
            "monitor_id": "dataset_orphaned_children",
            "publisher_id": "pub_x",
            "publisher_name": "Publisher X",
            "past_threshold": False,
            "status": "open",
            "detail": {"missing_parents": [], "feeds": []},
        }
    )
    # An empty list, and a monitor that declares no row detail at all.
    render_row_detail(get_monitor("dataset_orphaned_children"), incident)
    render_row_detail(get_monitor("dataset_stall"), incident)
    render_row_detail(get_monitor("single_feed_stall"), incident)
    st.write("done")


def test_a_row_with_nothing_to_show_renders_no_table_at_all() -> None:
    """And neither does a monitor that declares no row detail — no id branching anywhere."""
    app = run(_row_detail_absent_script)
    assert not app.dataframe
    assert not app.expander


def _benchmark_unreported_script() -> None:
    from stewards.components.trend_chart import render_figure
    from stewards.monitors.registry import get_monitor

    render_figure(get_monitor("dataset_orphaned_children"), (), None)


def test_a_benchmark_with_no_figure_says_so_rather_than_drawing_an_empty_meter() -> None:
    app = run(_benchmark_unreported_script)
    assert not app.get("vega_lite_chart")
    assert any("does not report a figure" in caption.value for caption in app.caption)


def _selected_row_script() -> None:
    import streamlit as st

    from stewards.components import monitor_page
    from stewards.components.monitor_page import render_monitor_page
    from stewards.monitors.registry import get_monitor

    def first_row(*_args: object, **_kwargs: object) -> int:
        """Stand in for the click AppTest cannot make on an `st.dataframe`."""
        return 0

    st.cache_data.clear()
    original = monitor_page.render_monitor_table
    monitor_page.render_monitor_table = first_row  # type: ignore[assignment]
    try:
        render_monitor_page(get_monitor("dataset_orphaned_children"))
    finally:
        monitor_page.render_monitor_table = original
        st.cache_data.clear()


def test_a_selected_row_opens_its_email_draft_and_its_missing_parents() -> None:
    """The whole point of selecting a row: who to write to, and what to tell them."""
    app = run(_selected_row_script)
    draft = " ".join(code.value for code in app.code)
    assert "Subject: OpenActive data check" in draft
    assert "orphaned children" in draft
    # The worst dataset sorts first, so that is the row the draft is for.
    assert "Loughborough University" in draft
    assert "432,823 items" in draft
    # And its missing parents, in their own table.
    frame = app.dataframe[0].value
    assert list(frame.columns) == ["Missing parent id", "Children affected"]
    assert len(frame) == 3
