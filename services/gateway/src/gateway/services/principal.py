"""Who is making a request, as established by authentication. Immutable and per-request."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


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

    @property
    def rate_limit_bucket(self) -> str:
        return f"user:{self.user_id}"
