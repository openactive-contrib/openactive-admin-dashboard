"""Settings resolution. Importable and callable without a Streamlit runtime."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_ALLOWED_DOMAIN = "theodi.org"
DEFAULT_THRESHOLD_DAYS = 7


class ConfigError(RuntimeError):
    """Settings are missing or contradictory."""


@dataclass(frozen=True, slots=True)
class Settings:
    api_base_url: str
    api_token: str = ""
    env: str = "prod"
    allowed_email_domain: str = DEFAULT_ALLOWED_DOMAIN
    contact_threshold_days: int = DEFAULT_THRESHOLD_DAYS
    use_sample_data: bool = False
    disable_auth: bool = False

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


_TRUE = frozenset({"1", "true", "yes", "on"})


def _flag(source: Mapping[str, str], key: str) -> bool:
    return source.get(key, "").strip().lower() in _TRUE


def load_settings(source: Mapping[str, str]) -> Settings:
    """Build settings from a flat string mapping (env vars or flattened secrets).

    `use_sample_data` serves bundled payloads instead of calling the API, so the app is
    runnable before the API exists. `disable_auth` is honoured only when `env` is `dev`.
    """
    env = source.get("STEWARDS_ENV", "prod").strip().lower() or "prod"
    use_sample_data = _flag(source, "STEWARDS_USE_SAMPLE_DATA")
    base_url = source.get("STEWARDS_API_BASE_URL", "").strip()
    token = source.get("STEWARDS_API_TOKEN", "").strip()

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
        env=env,
        allowed_email_domain=source.get("STEWARDS_ALLOWED_DOMAIN", "").strip()
        or DEFAULT_ALLOWED_DOMAIN,
        contact_threshold_days=threshold_days,
        use_sample_data=use_sample_data,
        disable_auth=env == "dev" and _flag(source, "STEWARDS_DISABLE_AUTH"),
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
