"""Request authentication for the WooCommerce REST API.

WooCommerce decides the scheme from the connection: over HTTPS it expects HTTP Basic auth with
the consumer key/secret; over plain HTTP it expects one-legged OAuth 1.0a with the OAuth
parameters in the query string (not the Authorization header). The signature base string is
built as in RFC 5849 section 3.4.1, matching ``WC_REST_Authentication::check_oauth_signature``:
parameters are sorted, each ``key=value`` pair is percent-encoded (so keys and values end up
encoded twice) and pairs are joined with ``%26``. The HMAC key is ``consumer_secret + "&"``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import quote

from pydantic import SecretStr


def rfc3986_encode(value: str) -> str:
    """Percent-encode like PHP ``rawurlencode`` (unreserved: ALPHA DIGIT - . _ ~)."""
    return quote(value, safe="~")


def encode_query(params: Mapping[str, str]) -> str:
    return "&".join(f"{rfc3986_encode(k)}={rfc3986_encode(v)}" for k, v in params.items())


@dataclass(frozen=True, slots=True)
class SignedRequest:
    query: dict[str, str]
    headers: dict[str, str]


class RequestAuthenticator(Protocol):
    def authenticate(self, method: str, url: str, query: Mapping[str, str]) -> SignedRequest:
        """Return the final query parameters and headers for one request attempt."""
        ...


class BasicAuthenticator:
    def __init__(self, consumer_key: SecretStr, consumer_secret: SecretStr) -> None:
        token = f"{consumer_key.get_secret_value()}:{consumer_secret.get_secret_value()}"
        self._header = "Basic " + base64.b64encode(token.encode()).decode("ascii")

    def authenticate(self, method: str, url: str, query: Mapping[str, str]) -> SignedRequest:
        return SignedRequest(query=dict(query), headers={"Authorization": self._header})

    def __repr__(self) -> str:
        return "BasicAuthenticator(<redacted>)"


class OAuth1Authenticator:
    """One-legged OAuth 1.0a signing as implemented by WooCommerce.

    A fresh nonce and timestamp are generated for every call, so retries are re-signed and the
    instance holds no per-request state (safe for concurrent use).
    """

    def __init__(
        self,
        consumer_key: SecretStr,
        consumer_secret: SecretStr,
        *,
        signature_method: Literal["HMAC-SHA256", "HMAC-SHA1"] = "HMAC-SHA256",
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._signature_method = signature_method
        self._digest = hashlib.sha256 if signature_method == "HMAC-SHA256" else hashlib.sha1
        self._clock = clock
        self._nonce_factory = nonce_factory

    def authenticate(self, method: str, url: str, query: Mapping[str, str]) -> SignedRequest:
        params = dict(query)
        params.update(
            {
                "oauth_consumer_key": self._consumer_key.get_secret_value(),
                "oauth_nonce": self._nonce_factory(),
                "oauth_signature_method": self._signature_method,
                "oauth_timestamp": str(int(self._clock())),
            }
        )
        params["oauth_signature"] = self.signature(method, url, params)
        return SignedRequest(query=params, headers={})

    def signature(self, method: str, url: str, params: Mapping[str, str]) -> str:
        pairs = sorted(
            (rfc3986_encode(k), rfc3986_encode(v))
            for k, v in params.items()
            if k != "oauth_signature"
        )
        param_string = "%26".join(rfc3986_encode(f"{k}={v}") for k, v in pairs)
        base_string = f"{method.upper()}&{rfc3986_encode(url)}&{param_string}"
        key = f"{self._consumer_secret.get_secret_value()}&".encode()
        digest = hmac.new(key, base_string.encode(), self._digest).digest()
        return base64.b64encode(digest).decode("ascii")

    def __repr__(self) -> str:
        return f"OAuth1Authenticator(signature_method={self._signature_method!r})"
