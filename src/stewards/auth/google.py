"""Google OIDC gate with a hard theodi.org allowlist.

The gate runs in `app.py` before `st.navigation(...).run()`, so no page module can be
reached unauthenticated. The decision is a pure function (`decide`) so the allowlist is
tested without a Streamlit session; `require_login` only renders the outcome.

Full user emails are never logged or displayed.
"""

from __future__ import annotations

from enum import StrEnum

import streamlit as st

from stewards.components.surface import SIDEBAR_FOOT_KEY, card
from stewards.config import Settings

LOGIN_EYEBROW = "Sign in"
LOGIN_TITLE = "Continue with your work account"
LOGIN_NOTE = "Single sign-on via Google. Platform never sees or stores your password."
LOGIN_FOOTER = "OpenActive admin dashboard reports the health and quality of the ecosystem."


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
    """The sign-in card: eyebrow, promise, the single SSO action, then the reassurance.

    Structure only — the card's type scale lives in `components/surface.py`, keyed off the
    container keys set here. The button carries a Material icon rather than the Google
    wordmark: the mark is multicoloured artwork whose colours are not in the brand palette,
    and `tests/unit/test_theme.py` holds the line that no other colour enters the app.
    """
    _, middle, _ = st.columns([1, 2, 1])
    with middle, card("login"):
        with st.container(key="oalogineyebrow"):
            st.markdown(LOGIN_EYEBROW.upper())
        with st.container(key="oalogintitle"):
            st.markdown(LOGIN_TITLE)
        with st.container(key="oaloginbody"):
            st.markdown(
                f"Access is limited to the **{domain}** Google Workspace. "
                "No password to remember."
            )
        with st.container(key="oaloginaction"):
            st.button(
                "Continue with Google",
                key="auth_login",
                type="primary",
                icon=":material/login:",
                on_click=st.login,
                width="stretch",
            )
        with st.container(key="oaloginnote"):
            st.markdown(f":material/verified_user: {LOGIN_NOTE}")
        st.divider()
        st.caption(LOGIN_FOOTER)


def render_denied(domain: str) -> None:
    st.error(f"Access is limited to the {domain} Google workspace.")
    st.button("Sign out", key="auth_signout_denied", on_click=st.logout)


def render_identity(email: str | None) -> None:
    """Sidebar identity block. Shows the masked address, never the full one.

    The container key is what `components/surface.py` targets to hold the block at the foot
    of the sidebar.
    """
    with st.sidebar, st.container(key=SIDEBAR_FOOT_KEY):
        st.divider()
        st.caption(mask_email(email))
        st.caption("Google SSO · steward")
        st.button("Sign out", key="auth_signout", on_click=st.logout, width="stretch")


def render_identity_footer(email: str | None) -> None:
    """The identity block, drawn once the navigation is already on the page.

    Order alone only puts it *below* the nav, not at the bottom of the sidebar; holding it
    there takes a rule in `components/surface.py`, because Streamlit offers no native way
    to pin a sidebar element to the bottom.

    A dev-bypass session has no identity to show, so it renders nothing at all rather than
    an "unknown" address.
    """
    if email is None:
        return
    render_identity(email)


def render_dev_bypass_warning() -> None:
    st.warning(
        "Authentication is disabled for local development. Never run a deployment in this "
        "mode.",
        icon=":material/lock_open:",
    )


def _current_email() -> str | None:
    """The signed-in address, or None if the session does not carry one.

    `st.user` raises for a key the session has no value for, and until the user signs in
    it carries no `email` at all — so it is read through the mapping interface, which
    defaults instead. The proxy is loosely typed; only a string is a usable identity.
    """
    email = st.user.get("email")
    return email if isinstance(email, str) else None


def require_login(settings: Settings) -> str | None:
    """Gate the app. Returns the signed-in email, or stops the script.

    In a dev environment the gate can be skipped explicitly with `STEWARDS_DISABLE_AUTH`;
    the banner says so on every page so it can never pass unnoticed in a real deployment.

    The returned address is what `render_identity_footer` renders once the navigation has
    been drawn, which is what puts sign-out at the bottom of the sidebar.
    """
    is_logged_in = bool(settings.disable_auth) or bool(st.user.get("is_logged_in"))
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
            return email
