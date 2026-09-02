"""Settings resolution. Importable and callable without a Streamlit runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from stewards.api.endpoints import Style

DEFAULT_ALLOWED_DOMAIN = "theodi.org"
DEFAULT_THRESHOLD_DAYS = 7
DEFAULT_DOCS_URL = "https://openactive-contrib.github.io/openactive-admin-dashboard/"


class ConfigError(RuntimeError):
    """Settings are missing or contradictory."""


@dataclass(frozen=True, slots=True)
class Settings:
    api_base_url: str
    api_token: str = ""
    api_style: Style = Style.CONTRACT
    api_token_param: str = ""
    """Query parameter the token is sent as. Empty means an `Authorization: Bearer` header,
    which is what the versioned contract expects; the interim admin API takes `?token=`."""

    env: str = "prod"
    allowed_email_domain: str = DEFAULT_ALLOWED_DOMAIN
    contact_threshold_days: int = DEFAULT_THRESHOLD_DAYS
    use_sample_data: bool = False
    disable_auth: bool = False
    docs_url: str = DEFAULT_DOCS_URL

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    @property
    def effective_api_style(self) -> Style:
        """The shape requests are actually built for.

        Sample-data mode always speaks the contract shape whatever `api_style` says: the
        bundled payloads are named for it, and they cover the summary and contact queue
        that the interim admin API has not implemented. Without this, pointing a local run
        at the admin API would silently disable those pages in sample mode too.
        """
        return Style.CONTRACT if self.use_sample_data else self.api_style


_TRUE = frozenset({"1", "true", "yes", "on"})


def _flag(source: Mapping[str, str], key: str) -> bool:
    return source.get(key, "").strip().lower() in _TRUE


def load_settings(source: Mapping[str, str]) -> Settings:
    """Build settings from a flat string mapping (env vars or flattened secrets).

    `use_sample_data` serves bundled payloads instead of calling the API, so the app is
    runnable before the API exists. `disable_auth` is honoured only when `env` is `dev`.
    `api_style` picks which URL shape the deployment's API speaks — see `api/endpoints.py`.
    """
    env = source.get("STEWARDS_ENV", "prod").strip().lower() or "prod"
    use_sample_data = _flag(source, "STEWARDS_USE_SAMPLE_DATA")
    base_url = source.get("STEWARDS_API_BASE_URL", "").strip()
    token = source.get("STEWARDS_API_TOKEN", "").strip()

    style_name = source.get("STEWARDS_API_STYLE", "").strip().lower() or Style.CONTRACT.value
    try:
        style = Style(style_name)
    except ValueError as exc:
        known = ", ".join(s.value for s in Style)
        raise ConfigError(
            f"STEWARDS_API_STYLE must be one of {known}, got {style_name!r}"
        ) from exc

    if not base_url:
        if not use_sample_data:
            raise ConfigError(
                "STEWARDS_API_BASE_URL is not set. Point it at the stewards API, or set "
                "STEWARDS_USE_SAMPLE_DATA=true to run against the bundled sample payloads."
            )
        base_url = "https://sample.invalid"

    threshold = source.get("STEWARDS_CONTACT_THRESHOLD_DAYS", "").strip()
    try:
        threshold_days = int(threshold) if threshold else DEFAULT_THRESHOLD_DAYS
    except ValueError as exc:
        raise ConfigError(
            f"STEWARDS_CONTACT_THRESHOLD_DAYS must be an integer, got {threshold!r}"
        ) from exc
    if threshold_days < 1:
        raise ConfigError("STEWARDS_CONTACT_THRESHOLD_DAYS must be >= 1")

    return Settings(
        api_base_url=base_url.rstrip("/"),
        api_token=token,
        api_style=style,
        api_token_param=source.get("STEWARDS_API_TOKEN_PARAM", "").strip(),
        env=env,
        allowed_email_domain=source.get("STEWARDS_ALLOWED_DOMAIN", "").strip()
        or DEFAULT_ALLOWED_DOMAIN,
        contact_threshold_days=threshold_days,
        use_sample_data=use_sample_data,
        disable_auth=env == "dev" and _flag(source, "STEWARDS_DISABLE_AUTH"),
        docs_url=source.get("STEWARDS_DOCS_URL", "").strip() or DEFAULT_DOCS_URL,
    )


def _secret_source() -> dict[str, str]:
    """Flatten `[stewards]` in .streamlit/secrets.toml to STEWARDS_* keys, if present."""
    try:
        import streamlit as st

        section = st.secrets.get("stewards", {})
    except Exception:  # no secrets file, or no Streamlit context — env only
        return {}
    return {f"STEWARDS_{key.upper()}": str(value) for key, value in dict(section).items()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings: environment variables override secrets.toml."""
    import os

    source: dict[str, str] = _secret_source()
    source.update({k: v for k, v in os.environ.items() if k.startswith("STEWARDS_")})
    return load_settings(source)
