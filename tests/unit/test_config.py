"""Settings resolution."""

from __future__ import annotations

import pytest

from stewards.api.endpoints import Style
from stewards.config import (
    DEFAULT_ALLOWED_DOMAIN,
    DEFAULT_DOCS_URL,
    DEFAULT_THRESHOLD_DAYS,
    ConfigError,
    Settings,
    load_settings,
)

BASE = {"STEWARDS_API_BASE_URL": "https://api.test/", "STEWARDS_API_TOKEN": "secret"}


def test_minimal_settings() -> None:
    settings = load_settings(BASE)
    assert settings.api_base_url == "https://api.test"  # trailing slash trimmed
    assert settings.api_token == "secret"
    assert settings.env == "prod"
    assert settings.allowed_email_domain == DEFAULT_ALLOWED_DOMAIN
    assert settings.contact_threshold_days == DEFAULT_THRESHOLD_DAYS
    assert not settings.use_sample_data
    assert not settings.disable_auth
    assert not settings.is_dev


def test_a_missing_base_url_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="STEWARDS_API_BASE_URL"):
        load_settings({})


def test_sample_data_mode_needs_no_base_url() -> None:
    settings = load_settings({"STEWARDS_USE_SAMPLE_DATA": "true"})
    assert settings.use_sample_data
    assert settings.api_base_url


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_flag_spellings(value: str) -> None:
    assert load_settings(BASE | {"STEWARDS_USE_SAMPLE_DATA": value}).use_sample_data


@pytest.mark.parametrize("value", ["0", "false", "no", "", "maybe"])
def test_everything_else_is_false(value: str) -> None:
    assert not load_settings(BASE | {"STEWARDS_USE_SAMPLE_DATA": value}).use_sample_data


def test_auth_can_only_be_disabled_in_dev() -> None:
    assert load_settings(BASE | {"STEWARDS_DISABLE_AUTH": "true"}).disable_auth is False
    dev = load_settings(BASE | {"STEWARDS_ENV": "dev", "STEWARDS_DISABLE_AUTH": "true"})
    assert dev.disable_auth
    assert dev.is_dev


def test_threshold_is_configurable() -> None:
    assert (
        load_settings(BASE | {"STEWARDS_CONTACT_THRESHOLD_DAYS": "14"}).contact_threshold_days
        == 14
    )


def test_threshold_boundary_of_one_day_is_allowed() -> None:
    assert (
        load_settings(BASE | {"STEWARDS_CONTACT_THRESHOLD_DAYS": "1"}).contact_threshold_days
        == 1
    )


@pytest.mark.parametrize("value", ["0", "-3"])
def test_a_non_positive_threshold_is_rejected(value: str) -> None:
    with pytest.raises(ConfigError, match=">= 1"):
        load_settings(BASE | {"STEWARDS_CONTACT_THRESHOLD_DAYS": value})


def test_a_non_numeric_threshold_is_rejected() -> None:
    with pytest.raises(ConfigError, match="must be an integer"):
        load_settings(BASE | {"STEWARDS_CONTACT_THRESHOLD_DAYS": "seven"})


def test_an_empty_threshold_falls_back_to_the_default() -> None:
    assert (
        load_settings(BASE | {"STEWARDS_CONTACT_THRESHOLD_DAYS": " "}).contact_threshold_days
        == 7
    )


def test_allowed_domain_is_overridable() -> None:
    assert (
        load_settings(BASE | {"STEWARDS_ALLOWED_DOMAIN": "example.org"}).allowed_email_domain
        == "example.org"
    )


def test_settings_are_frozen(settings: Settings) -> None:
    with pytest.raises(AttributeError):
        settings.api_token = "changed"  # type: ignore[misc]


def test_secrets_are_read_when_present(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """`[stewards]` in secrets.toml is flattened to STEWARDS_* keys; env still wins."""
    import streamlit as st

    from stewards import config

    monkeypatch.setattr(
        st,
        "secrets",
        {"stewards": {"api_base_url": "https://from-secrets", "api_token": "s3cret"}},
    )
    monkeypatch.delenv("STEWARDS_API_BASE_URL", raising=False)
    monkeypatch.delenv("STEWARDS_API_TOKEN", raising=False)
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().api_base_url == "https://from-secrets"
        config.get_settings.cache_clear()
        monkeypatch.setenv("STEWARDS_API_BASE_URL", "https://from-env")
        assert config.get_settings().api_base_url == "https://from-env"
    finally:
        config.get_settings.cache_clear()


def test_a_missing_secrets_file_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import streamlit as st

    from stewards import config

    class Exploding:
        def get(self, _key: str, _default: object = None) -> object:
            raise FileNotFoundError("no secrets.toml")

    monkeypatch.setattr(st, "secrets", Exploding())
    monkeypatch.setenv("STEWARDS_API_BASE_URL", "https://from-env")
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().api_base_url == "https://from-env"
    finally:
        config.get_settings.cache_clear()


def test_the_docs_url_defaults_to_the_projects_github_pages_site() -> None:
    settings = load_settings({"STEWARDS_API_BASE_URL": "https://api.test"})
    assert settings.docs_url == DEFAULT_DOCS_URL


def test_the_docs_url_is_configurable() -> None:
    settings = load_settings(
        {
            "STEWARDS_API_BASE_URL": "https://api.test",
            "STEWARDS_DOCS_URL": "https://docs.theodi.org/stewards/ ",
        }
    )
    assert settings.docs_url == "https://docs.theodi.org/stewards/"


def test_a_blank_docs_url_falls_back_to_the_default() -> None:
    settings = load_settings(
        {"STEWARDS_API_BASE_URL": "https://api.test", "STEWARDS_DOCS_URL": "   "}
    )
    assert settings.docs_url == DEFAULT_DOCS_URL


# --- the API shape ------------------------------------------------------------------------


def test_the_api_shape_defaults_to_the_versioned_contract() -> None:
    settings = load_settings(BASE)
    assert settings.api_style is Style.CONTRACT
    assert settings.api_token_param == ""  # so the token goes in the header


def test_the_admin_shape_is_selectable_with_a_query_parameter_token() -> None:
    settings = load_settings(
        BASE | {"STEWARDS_API_STYLE": "ADMIN", "STEWARDS_API_TOKEN_PARAM": " token "}
    )
    assert settings.api_style is Style.ADMIN
    assert settings.api_token_param == "token"


def test_an_unknown_api_shape_is_a_config_error_naming_the_options() -> None:
    with pytest.raises(ConfigError, match="contract, admin"):
        load_settings(BASE | {"STEWARDS_API_STYLE": "graphql"})


def test_a_blank_api_shape_falls_back_to_the_contract() -> None:
    assert load_settings(BASE | {"STEWARDS_API_STYLE": "  "}).api_style is Style.CONTRACT


def test_sample_data_mode_speaks_the_contract_shape_whatever_the_style_says() -> None:
    """The bundled payloads are named for the contract, and cover endpoints admin lacks."""
    settings = load_settings(
        {"STEWARDS_USE_SAMPLE_DATA": "true", "STEWARDS_API_STYLE": "admin"}
    )
    assert settings.api_style is Style.ADMIN  # what the deployment is configured for
    assert settings.effective_api_style is Style.CONTRACT  # what requests are built for


def test_a_live_deployment_uses_the_style_it_declares() -> None:
    settings = load_settings(BASE | {"STEWARDS_API_STYLE": "admin"})
    assert settings.effective_api_style is Style.ADMIN
