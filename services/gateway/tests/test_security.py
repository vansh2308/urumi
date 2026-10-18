"""Credential primitives and the upstream URL allow-list (no database needed)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from gateway.config import UpstreamSettings
from gateway.security import PasswordService, TokenHasher, TokenKind


def test_issued_tokens_are_prefixed_random_and_stored_only_as_a_keyed_hash() -> None:
    hasher = TokenHasher(SecretStr("p" * 40))

    first = hasher.issue(TokenKind.API_KEY)
    second = hasher.issue(TokenKind.API_KEY)

    assert first.raw.startswith("urk_")
    assert first.raw != second.raw
    assert first.digest == hasher.digest(first.raw)
    assert first.raw not in first.digest
    assert first.display_prefix == first.raw[:12]
    # A different pepper gives a different digest: a database dump alone cannot verify guesses.
    assert TokenHasher(SecretStr("q" * 40)).digest(first.raw) != first.digest


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("ugs_" + "a" * 40, TokenKind.SESSION),
        ("urk_" + "a" * 40, TokenKind.API_KEY),
        ("uri_" + "a" * 40, TokenKind.INVITATION),
        ("ck_" + "a" * 40, None),  # WooCommerce consumer keys are never gateway credentials
        ("urk_short", None),
        ("urk_" + "a" * 200, None),
    ],
)
def test_token_kind_detection(raw: str, kind: TokenKind | None) -> None:
    assert TokenHasher.kind_of(raw) is kind


async def test_password_hashing_round_trip() -> None:
    passwords = PasswordService()

    hashed = await passwords.hash("correct horse battery staple")

    assert await passwords.verify(hashed, "correct horse battery staple")
    assert not await passwords.verify(hashed, "wrong")
    assert not await passwords.verify(None, "correct horse battery staple")


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("http://woocommerce-mcp.urumi.svc.cluster.local:8000/mcp", True),
        ("https://mcp.example.com/mcp", True),
        ("http://woocommerce-mcp.urumi.svc.cluster.local:9000/mcp", False),
        ("http://169.254.169.254/latest/meta-data", False),
        ("http://evil.example.com:8000/mcp", False),
        ("http://mcp.example.com/mcp", False),  # plain HTTP only where explicitly allowed
        ("file:///etc/passwd", False),
        ("http://user@woocommerce-mcp.urumi.svc.cluster.local.evil.com:8000/", False),
    ],
)
def test_upstream_allow_list(url: str, allowed: bool) -> None:
    settings = UpstreamSettings(
        _env_file=None,
        allowed_hosts=["woocommerce-mcp.urumi.svc.cluster.local:8000", "mcp.example.com:443"],
        allow_insecure_http=True,
    )
    assert settings.is_allowed_url(url) is allowed
