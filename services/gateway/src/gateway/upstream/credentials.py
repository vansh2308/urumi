"""Static credentials the gateway attaches to calls to one upstream MCP server.

A credential is one HTTP header (``Authorization: Bearer ...`` or a custom header such as
``X-Api-Key``). Its value is encrypted at rest with a key from ``UPSTREAM_CREDENTIAL_KEYS``
(a Kubernetes Secret in a cluster), decrypted only to build an upstream request, and never
returned by the API, logged, or sent to anything but that connector's own URL.

These are downstream integration credentials. They are unrelated to the platform API keys the
gateway issues to its own clients (``urk_...``), which are only ever stored as hashes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr

from gateway.errors import InvalidInputError

_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
# Headers the transport or the gateway itself owns; a credential may never override them.
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "content-type",
        "accept",
        "connection",
        "transfer-encoding",
        "upgrade",
        "te",
        "trailer",
        "keep-alive",
        "proxy-authorization",
        "proxy-connection",
        "cookie",
        "user-agent",
        "x-request-id",
        "mcp-session-id",
        "mcp-protocol-version",
        "last-event-id",
    }
)
MAX_VALUE_LENGTH = 4096


@dataclass(frozen=True, slots=True)
class UpstreamCredential:
    header_name: str
    value: str = field(repr=False)

    @classmethod
    def bearer(cls, token: str) -> UpstreamCredential:
        return cls.header("Authorization", f"Bearer {token.strip()}")

    @classmethod
    def header(cls, name: str, value: str) -> UpstreamCredential:
        name = name.strip()
        if not _HEADER_NAME_RE.match(name) or name.lower() in _RESERVED_HEADERS:
            raise InvalidInputError("The credential header name is not allowed.")
        value = value.strip()
        if not value or len(value) > MAX_VALUE_LENGTH:
            raise InvalidInputError(
                f"The credential value must be 1 to {MAX_VALUE_LENGTH} characters."
            )
        if any(ch in value for ch in "\r\n\0"):
            raise InvalidInputError("The credential value may not contain line breaks.")
        return cls(header_name=name, value=value)


class CredentialCipher:
    """Encrypts credential values (Fernet: AES-128-CBC + HMAC-SHA256).

    Several keys may be configured: the first encrypts, all of them decrypt, so a key can be
    rotated by prepending a new one, re-saving credentials, then dropping the old key.
    """

    def __init__(self, keys: list[SecretStr]) -> None:
        self._fernet = MultiFernet([Fernet(k.get_secret_value()) for k in keys]) if keys else None

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise InvalidInputError(
                "Upstream credentials are disabled: the gateway has no UPSTREAM_CREDENTIAL_KEYS."
            )
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        if self._fernet is None:
            raise CredentialUnavailableError("no credential key configured")
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise CredentialUnavailableError("credential cannot be decrypted") from exc


class CredentialUnavailableError(Exception):
    """A stored credential cannot be decrypted (key missing or rotated away)."""
