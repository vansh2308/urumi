"""Who is making a request, as established by authentication. Immutable and per-request."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from gateway.db.models import ApiKeyScope
from gateway.errors import PermissionDeniedError


class CredentialKind(StrEnum):
    SESSION = "session"
    API_KEY = "api_key"


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    email: str
    display_name: str
    is_platform_admin: bool
    credential: CredentialKind
    session_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None
    # An API key is bound to exactly one team; sessions are not bound to any.
    api_key_team_id: uuid.UUID | None = None
    api_key_scopes: frozenset[ApiKeyScope] = field(default_factory=frozenset)
    # "connector/tool" or "connector/*" entries; None means the key does not narrow access.
    api_key_tool_restrictions: tuple[str, ...] | None = None
    # The key's team limit, if the team set one.
    rate_limit_per_minute: int | None = None

    @property
    def rate_limit_bucket(self) -> str:
        if self.api_key_id is not None:
            return f"key:{self.api_key_id}"
        return f"user:{self.user_id}"

    def require_scope(self, scope: ApiKeyScope) -> None:
        """API keys need the scope explicitly; user sessions are not scope-limited."""
        if self.credential is CredentialKind.API_KEY and scope not in self.api_key_scopes:
            raise PermissionDeniedError(f"This API key lacks the '{scope.value}' scope.")
