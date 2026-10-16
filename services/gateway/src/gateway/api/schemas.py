"""Request and response bodies of the REST API.

Request models forbid unknown fields, so a payload cannot smuggle in attributes such as
``role`` or ``is_platform_admin`` that the endpoint does not accept.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


DisplayName = Annotated[str, Field(min_length=1, max_length=100)]
Password = Annotated[str, Field(min_length=1, max_length=256)]


# --- auth ---------------------------------------------------------------------------------------


class RegisterRequest(RequestModel):
    email: EmailStr
    password: Password
    display_name: DisplayName


class LoginRequest(RequestModel):
    email: EmailStr
    password: Password


class UserOut(ResponseModel):
    id: uuid.UUID
    email: str
    display_name: str
    is_platform_admin: bool
    created_at: datetime


class SessionOut(BaseModel):
    access_token: str = Field(description="Bearer token. Shown once; store it securely.")
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_at: datetime
    user: UserOut


class MeOut(BaseModel):
    user: UserOut
    credential: str
