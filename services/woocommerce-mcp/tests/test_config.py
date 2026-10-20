"""Settings validation: the connect URL and the production HTTPS rule."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from woocommerce_mcp.config import (
    AppSettings,
    DatabaseSettings,
    Environment,
    OperationSettings,
    ServerSettings,
    Settings,
    WooCommerceSettings,
)


def _wc(**overrides: Any) -> WooCommerceSettings:
    values: dict[str, Any] = {
        "base_url": "http://acme.localtest.me",
        "consumer_key": "ck_test",
        "consumer_secret": "cs_test",
        "allow_insecure_http": True,
    } | overrides
    return WooCommerceSettings(_env_file=None, **values)


def _settings(env: Environment, wc: WooCommerceSettings) -> Settings:
    return Settings(
        app=AppSettings(_env_file=None, app_env=env),
        server=ServerSettings(_env_file=None),
        woocommerce=wc,
        database=DatabaseSettings(_env_file=None, url="postgresql://u:p@localhost/db"),
        operations=OperationSettings(_env_file=None),
    )


def test_connect_url_is_normalised_to_an_origin() -> None:
    wc = _wc(connect_url="http://store-wordpress.store-acme.svc.cluster.local/")

    assert wc.connect_url == "http://store-wordpress.store-acme.svc.cluster.local"
    assert wc.connects_in_cluster is True
    assert wc.api_base_url == "http://acme.localtest.me/wp-json/wc/v3"


def test_blank_connect_url_means_none() -> None:
    assert _wc(connect_url="").connect_url is None
    assert _wc().connects_in_cluster is False


@pytest.mark.parametrize(
    "value", ["ftp://wordpress", "http://wordpress/sub", "http://wordpress?x=1", "wordpress"]
)
def test_connect_url_rejects_anything_but_an_origin(value: str) -> None:
    with pytest.raises(ValidationError):
        _wc(connect_url=value)


def test_production_allows_http_only_to_an_in_cluster_service() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        _settings(Environment.PRODUCTION, _wc())
    with pytest.raises(ValueError, match="HTTPS"):
        _settings(Environment.PRODUCTION, _wc(connect_url="http://10.0.0.5"))

    settings = _settings(
        Environment.PRODUCTION,
        _wc(connect_url="http://store-wordpress.store-acme.svc.cluster.local"),
    )
    assert settings.woocommerce.connects_in_cluster


def test_store_name_whitespace_is_collapsed() -> None:
    assert AppSettings(_env_file=None, store_name="  Acme   Shop ").store_name == "Acme Shop"
    assert AppSettings(_env_file=None, store_name="   ").store_name is None
