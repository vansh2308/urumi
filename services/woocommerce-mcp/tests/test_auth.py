"""WooCommerce request authentication: OAuth 1.0a over HTTP, Basic over HTTPS."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from urllib.parse import quote

from pydantic import SecretStr

from woocommerce_mcp.woocommerce.auth import BasicAuthenticator, OAuth1Authenticator


def _php_rawurlencode(value: str) -> str:
    return quote(value, safe="-_.~")


def _woocommerce_reference_signature(
    method: str, url: str, params: Mapping[str, str], secret: str
) -> str:
    """Transcription of ``WC_REST_Authentication::check_oauth_signature``: sort by raw key,
    rawurlencode keys and values, join each pair with '=', rawurlencode the pair again, join with
    '%26', and sign ``METHOD&rawurlencode(url)&pairs`` with HMAC-SHA256 keyed ``secret&``."""
    normalised = [
        (_php_rawurlencode(k), _php_rawurlencode(v))
        for k, v in sorted(params.items())
        if k != "oauth_signature"
    ]
    query_string = "%26".join(_php_rawurlencode(f"{k}={v}") for k, v in normalised)
    to_sign = f"{method}&{_php_rawurlencode(url)}&{query_string}"
    digest = hmac.new(f"{secret}&".encode(), to_sign.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _oauth(**kwargs: object) -> OAuth1Authenticator:
    return OAuth1Authenticator(
        SecretStr("ck_example"),
        SecretStr("cs_example"),
        clock=lambda: 1_760_000_000.0,
        **kwargs,  # type: ignore[arg-type]
    )


def test_oauth1_signature_matches_woocommerce_algorithm() -> None:
    url = "http://wordpress.local/wp-json/wc/v3/products"
    query = {"search": "blue mug & co", "per_page": "20", "_fields": "id,name"}
    signed = _oauth(nonce_factory=lambda: "fixednonce").authenticate("GET", url, query)

    assert signed.headers == {}
    assert signed.query["oauth_consumer_key"] == "ck_example"
    assert signed.query["oauth_signature_method"] == "HMAC-SHA256"
    assert signed.query["oauth_timestamp"] == "1760000000"
    assert signed.query["search"] == "blue mug & co"
    assert signed.query["oauth_signature"] == _woocommerce_reference_signature(
        "GET", url, signed.query, "cs_example"
    )


def test_oauth1_signs_each_attempt_with_a_fresh_nonce() -> None:
    nonces = iter(["nonce-one", "nonce-two"])
    auth = _oauth(nonce_factory=lambda: next(nonces))
    url = "http://wordpress.local/wp-json/wc/v3/orders/7"

    first = auth.authenticate("PUT", url, {})
    second = auth.authenticate("PUT", url, {})

    assert first.query["oauth_nonce"] != second.query["oauth_nonce"]
    assert first.query["oauth_signature"] != second.query["oauth_signature"]


def test_basic_auth_header_and_redacted_repr() -> None:
    auth = BasicAuthenticator(SecretStr("ck_example"), SecretStr("cs_example"))

    signed = auth.authenticate("GET", "https://shop.example/wp-json/wc/v3/products", {"page": "1"})

    expected = base64.b64encode(b"ck_example:cs_example").decode()
    assert signed.headers == {"Authorization": f"Basic {expected}"}
    assert signed.query == {"page": "1"}
    assert "cs_example" not in repr(auth)
    assert "cs_example" not in repr(_oauth())
