"""The domain allowlist and the audit-safe email masking."""

from __future__ import annotations

import pytest

from stewards.auth.google import Decision, decide, is_allowed, mask_email
from stewards.config import Settings


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("huseyin.kir@theodi.org", True),
        ("HUSEYIN.KIR@THEODI.ORG", True),
        ("  steward@theodi.org  ", True),
        ("steward@nottheodi.org", False),
        ("steward@theodi.org.evil.com", False),
        ("steward@sub.theodi.org", False),
        ("theodi.org", False),
        ("@theodi.org", False),
        ("", False),
        (None, False),
    ],
)
def test_is_allowed(email: str | None, expected: bool) -> None:
    assert is_allowed(email, "theodi.org") is expected


def test_allowlist_domain_comparison_is_case_insensitive() -> None:
    assert is_allowed("steward@theodi.org", "TheODI.org")


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("huseyin.kir@theodi.org", "h…@theodi.org"),
        ("a@theodi.org", "a…@theodi.org"),
        ("not-an-email", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_mask_email_never_reveals_the_full_local_part(email: str | None, expected: str) -> None:
    assert mask_email(email) == expected


def settings_for(**overrides: object) -> Settings:
    return Settings(api_base_url="https://api.test", **overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("is_logged_in", "email", "expected"),
    [
        (False, None, Decision.LOGIN_REQUIRED),
        (False, "steward@theodi.org", Decision.LOGIN_REQUIRED),
        (True, None, Decision.DENIED),
        (True, "steward@gmail.com", Decision.DENIED),
        (True, "steward@theodi.org", Decision.ALLOWED),
    ],
)
def test_decide(is_logged_in: bool, email: str | None, expected: Decision) -> None:
    decision = decide(settings_for(), is_logged_in=is_logged_in, email=email)
    assert decision is expected


def test_the_dev_bypass_short_circuits_everything() -> None:
    settings = settings_for(env="dev", disable_auth=True)
    assert decide(settings, is_logged_in=False, email=None) is Decision.DEV_BYPASS


def test_the_bypass_cannot_be_reached_outside_dev() -> None:
    """`Settings` refuses to set the flag outside dev, so the gate cannot be bypassed."""
    from stewards.config import load_settings

    settings = load_settings(
        {"STEWARDS_API_BASE_URL": "https://api.test", "STEWARDS_DISABLE_AUTH": "true"}
    )
    assert decide(settings, is_logged_in=False, email=None) is Decision.LOGIN_REQUIRED


def test_the_allowed_domain_is_configurable() -> None:
    settings = settings_for(allowed_email_domain="example.org")
    assert decide(settings, is_logged_in=True, email="a@example.org") is Decision.ALLOWED
    assert decide(settings, is_logged_in=True, email="a@theodi.org") is Decision.DENIED


def test_current_email_is_none_when_the_session_carries_no_email() -> None:
    """Regression: reading `st.user.email` raises for a key the session does not carry.

    Once an `[auth]` section exists in secrets.toml, Streamlit populates `st.user` with
    nothing but `is_logged_in` until the user signs in — so the gate crashed on the way to
    rendering the login screen. Outside a script run `st.user` is empty, the same shape.
    """
    from stewards.auth.google import _current_email

    assert _current_email() is None
