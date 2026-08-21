"""Renders of the components a page cannot reach from AppTest alone.

Row selection, error states and the login screen are all real UI states that no unit test
can exercise, so they are driven here through `AppTest.from_function`.
"""

from __future__ import annotations

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from stewards.api.errors import (
    ApiContractError,
    ApiError,
    ApiNotFound,
    ApiUnauthorized,
    ApiUnavailable,
)
from stewards.api.models import Meta, Summary, SummaryResponse
from stewards.api.sample_transport import load_sample

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
    from stewards.api.models import IncidentPage
    from stewards.api.sample_transport import load_sample
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
        (ApiNotFound("no endpoint"), "no data for this monitor yet"),
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
    assert app.title[0].value == "Single-feed stalls"


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
    assert any("fleet size unknown" in c.value for c in app.caption)


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


def test_a_header_without_an_export_renders_no_download_button() -> None:
    app = run(_header_script)
    assert not app.get("download_button")
    assert any("Snapshot 2026-08-21 06:00" in c.value for c in app.caption)


def _export_script() -> None:
    from stewards.api.models import IncidentPage
    from stewards.api.sample_transport import load_sample
    from stewards.components import layout
    from stewards.monitors.registry import get_monitor
    from stewards.monitors.transforms import to_dataframe

    monitor = get_monitor("http_failure")
    page = IncidentPage.model_validate(load_sample("http_failure_incidents"))
    frame = to_dataframe(monitor, page.data)
    layout.render_header(
        monitor.crumb, monitor.name, page.meta, export=frame, export_name=monitor.id
    )


def test_the_export_button_is_named_after_the_snapshot() -> None:
    app = run(_export_script)
    assert app.get("download_button")


# --- the empty trend chart --------------------------------------------------------------


def _empty_trend_script() -> None:
    from stewards.components.trend_chart import render_trend
    from stewards.monitors.registry import get_monitor

    render_trend(get_monitor("http_failure"), [])


def test_an_empty_trend_says_so_instead_of_charting_nothing() -> None:
    app = run(_empty_trend_script)
    assert any("No trend history" in c.value for c in app.caption)


# --- the auth gate ----------------------------------------------------------------------


def _login_screen_script() -> None:
    from stewards.auth.google import render_denied, render_identity, render_login_screen

    render_login_screen("theodi.org")
    render_denied("theodi.org")
    render_identity("huseyin.kir@theodi.org")


def test_the_login_screen_names_the_workspace_and_never_shows_a_full_address() -> None:
    app = run(_login_screen_script)
    assert any("Continue with Google" in b.label for b in app.button)
    assert any("theodi.org" in e.value for e in app.error)
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
    assert nav.page_for("docs") is not None
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
        "http_failure": "http_failures",
        "docs": "docs",
    }
    actual = {key: nav.page_for(key).url_path for key in expected}
    assert actual == expected, actual


def test_navigation_builds_a_page_per_registry_entry_with_count_badges() -> None:
    run(_navigation_script)


def test_the_page_url_paths_drop_the_numeric_prefix() -> None:
    """The nav filenames are ordered `12_http_failures.py`; the route is `/http_failures`."""
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
            "http_failure": NavBadge("9", Tone.AMBER),
        }
    )


def test_the_sidebar_renders_a_link_per_page_and_a_pill_per_count() -> None:
    app = run(_sidebar_script)
    captions = [c.value for c in app.caption]
    for section in ("OVERVIEW", "AVAILABILITY", "KNOWLEDGE BASE"):
        assert section in captions
    markdown = " ".join(m.value for m in app.markdown)
    assert "Data Stewards" in markdown
    # Badges render as markdown colour directives carrying the count.
    for count, colour in (("10", "red"), ("23", "red"), ("9", "orange")):
        assert f":{colour}-badge[{count}]" in markdown


def _sidebar_without_badges_script() -> None:
    from stewards.components import nav

    nav.build_navigation()
    nav.render_sidebar({})
    assert nav.page_for("single_feed_stall") is not None


def test_the_sidebar_still_renders_when_the_summary_is_unavailable() -> None:
    app = run(_sidebar_without_badges_script)
    markdown = " ".join(m.value for m in app.markdown)
    assert "Data Stewards" in markdown
    # The brand mark is a badge; no *count* pill should appear.
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


# --- the docs page ----------------------------------------------------------------------


def _stale_doc_script() -> None:
    import streamlit as st

    from stewards.components.docs_page import SELECTED_KEY, render_docs_page

    st.session_state[SELECTED_KEY] = "a-document-that-was-deleted"
    render_docs_page()


def test_a_stale_document_selection_falls_back_to_the_index() -> None:
    app = run(_stale_doc_script)
    assert any("no longer exists" in w.value for w in app.warning)
    assert any("Internal documentation" in t.value for t in app.title)


def _internal_only_doc_script() -> None:
    from stewards.components.docs_page import render_doc
    from stewards.knowledge.loader import get_doc

    render_doc(get_doc("single-feed-stalls-runbook"))


def test_a_restricted_document_is_flagged_and_lists_its_headings() -> None:
    app = run(_internal_only_doc_script)
    markdown = " ".join(m.value for m in app.markdown)
    assert "internal only" in markdown
    assert "Detection" in markdown
    assert "Triage sequence" in markdown
