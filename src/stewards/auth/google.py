"""Google OIDC gate with a hard theodi.org allowlist.

The gate runs in `app.py` before `st.navigation(...).run()`, so no page module can be
reached unauthenticated. The decision is a pure function (`decide`) so the allowlist is
tested without a Streamlit session; `require_login` only renders the outcome.

Full user emails are never logged or displayed.
"""

from __future__ import annotations

from enum import StrEnum

import streamlit as st

from stewards.components.surface import card
from stewards.config import Settings


class Decision(StrEnum):
    DEV_BYPASS = "dev_bypass"
    LOGIN_REQUIRED = "login_required"
    DENIED = "denied"
    ALLOWED = "allowed"


def is_allowed(email: str | None, domain: str) -> bool:
    """True only for a well-formed address in the allowed workspace domain."""
    if not email:
        return False
    local, _, host = email.strip().lower().rpartition("@")
    return bool(local) and host == domain.strip().lower()


def decide(settings: Settings, *, is_logged_in: bool, email: str | None) -> Decision:
    """What the gate should do, given the session state. No Streamlit calls."""
    if settings.disable_auth:
        return Decision.DEV_BYPASS
    if not is_logged_in:
        return Decision.LOGIN_REQUIRED
    if not is_allowed(email, settings.allowed_email_domain):
        return Decision.DENIED
    return Decision.ALLOWED


def mask_email(email: str | None) -> str:
    """Audit-safe rendering: first character of the local part plus the domain."""
    if not email or "@" not in email:
        return "unknown"
    local, _, host = email.partition("@")
    return f"{local[:1]}…@{host}"


def render_login_screen(domain: str) -> None:
    _, middle, _ = st.columns([1, 2, 1])
    with middle, card("login"):
        st.title("Data Stewards", anchor=False)
        st.caption("OpenActive · internal")
        st.markdown(
            "This dashboard reports the health of the OpenActive publisher feeds. "
            f"Access is limited to the {domain} Google workspace."
        )
        st.button(
            "Continue with Google",
            key="auth_login",
            type="primary",
            on_click=st.login,
            width="stretch",
        )


def render_denied(domain: str) -> None:
    st.error(f"Access is limited to the {domain} Google workspace.")
    st.button("Sign out", key="auth_signout_denied", on_click=st.logout)


def render_identity(email: str | None) -> None:
    """Sidebar identity block. Shows the masked address, never the full one."""
    with st.sidebar:
        st.divider()
        st.caption(mask_email(email))
        st.caption("Google SSO · steward")
        st.button("Sign out", key="auth_signout", on_click=st.logout, width="stretch")


def render_dev_bypass_warning() -> None:
    st.warning(
        "Authentication is disabled for local development. Never run a deployment in this "
        "mode.",
        icon=":material/lock_open:",
    )


def _current_email() -> str | None:
    """`st.user.email` is loosely typed; only a string is a usable identity."""
    email = st.user.email
    return email if isinstance(email, str) else None


def require_login(settings: Settings) -> str | None:
    """Gate the app. Returns the signed-in email, or stops the script.

    In a dev environment the gate can be skipped explicitly with `STEWARDS_DISABLE_AUTH`;
    the banner says so on every page so it can never pass unnoticed in a real deployment.
    """
    is_logged_in = bool(settings.disable_auth) or bool(st.user.is_logged_in)
    email = None if settings.disable_auth else _current_email()
    decision = decide(settings, is_logged_in=is_logged_in, email=email)

    match decision:
        case Decision.DEV_BYPASS:
            render_dev_bypass_warning()
            return None
        case Decision.LOGIN_REQUIRED:
            render_login_screen(settings.allowed_email_domain)
            st.stop()
        case Decision.DENIED:
            render_denied(settings.allowed_email_domain)
            st.stop()
        case _:
            render_identity(email)
            return email
