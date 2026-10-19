"""Credential primitives: password hashing and opaque token generation/verification.

Three kinds of opaque secrets are issued, each with its own visible prefix so they are easy to
recognise in secret scanners and impossible to confuse with downstream credentials such as
WooCommerce ``ck_``/``cs_`` keys (which never pass through the gateway):

* ``ugs_`` user session tokens (dashboard / management API),
* ``urk_`` platform API keys (tool discovery and invocation),
* ``uri_`` invitation tokens.

Only ``HMAC-SHA256(pepper, token)`` is stored. The tokens carry 256 bits of randomness, so a
fast keyed hash is the right tool (a slow password hash would only add latency); the pepper
means a database dump alone cannot be used to test guesses. Lookup is by the digest, so no
secret-dependent comparison happens in Python.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from enum import StrEnum

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import SecretStr


class TokenKind(StrEnum):
    SESSION = "ugs_"
    API_KEY = "urk_"
    INVITATION = "uri_"
    OAUTH_REQUEST = "uoq_"  # pending authorization request, carried through the consent page
    OAUTH_CODE = "uoc_"  # authorization code (single use, minutes)
    OAUTH_REFRESH = "uor_"  # refresh token (rotated on every use)


_TOKEN_BYTES = 32
_DISPLAY_PREFIX_LEN = 12  # "urk_" + 8 random chars: enough to tell keys apart, useless to guess


@dataclass(frozen=True, slots=True)
class IssuedToken:
    raw: str
    digest: str
    display_prefix: str


class TokenHasher:
    def __init__(self, pepper: SecretStr) -> None:
        self._pepper = pepper.get_secret_value().encode()

    def digest(self, raw: str) -> str:
        return hmac.new(self._pepper, raw.encode(), hashlib.sha256).hexdigest()

    def issue(self, kind: TokenKind) -> IssuedToken:
        raw = kind.value + secrets.token_urlsafe(_TOKEN_BYTES)
        return IssuedToken(
            raw=raw, digest=self.digest(raw), display_prefix=raw[:_DISPLAY_PREFIX_LEN]
        )

    @staticmethod
    def kind_of(raw: str) -> TokenKind | None:
        for kind in TokenKind:
            if raw.startswith(kind.value) and 20 <= len(raw) <= 128:
                return kind
        return None


class PasswordService:
    """Argon2id hashing, run off the event loop because it is deliberately CPU-expensive."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()
        # Verified against when the account does not exist, so response time does not reveal
        # whether an email is registered.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str | None, password: str) -> bool:
        return await asyncio.to_thread(self._verify, password_hash, password)

    def _verify(self, password_hash: str | None, password: str) -> bool:
        try:
            self._hasher.verify(password_hash or self._dummy_hash, password)
        except (VerificationError, InvalidHashError):
            return False
        return password_hash is not None

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
