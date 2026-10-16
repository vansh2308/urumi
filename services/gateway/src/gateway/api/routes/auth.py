"""Registration, login/logout and the caller's own profile."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from gateway.api.deps import (
    API_PREFIX,
    ContainerDep,
    PrincipalDep,
    SessionPrincipalDep,
    client_ip,
)
from gateway.api.schemas import LoginRequest, MeOut, RegisterRequest, SessionOut, UserOut
from gateway.services.auth import normalise_email

router = APIRouter(prefix=API_PREFIX, tags=["auth"])


async def _throttle_auth(container: ContainerDep, request: Request, email: str) -> None:
    limit = container.settings.rate_limit.auth_per_minute
    limiter = container.rate_limiter
    await limiter.hit(f"auth-ip:{client_ip(request)}", limit=limit * 3, scope="auth")
    await limiter.hit(f"auth-email:{normalise_email(email)}", limit=limit, scope="auth")


@router.post("/auth/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def register(body: RegisterRequest, request: Request, container: ContainerDep) -> UserOut:
    """Create an account. Disabled when ``AUTH_ALLOW_SIGNUP=false``."""
    await _throttle_auth(container, request, body.email)
    user = await container.auth.register(
        email=body.email, password=body.password, display_name=body.display_name
    )
    return UserOut.model_validate(user)


@router.post("/auth/login", response_model=SessionOut)
async def login(body: LoginRequest, request: Request, container: ContainerDep) -> SessionOut:
    """Exchange email and password for a session bearer token (``ugs_...``)."""
    await _throttle_auth(container, request, body.email)
    issued = await container.auth.login(
        email=body.email,
        password=body.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return SessionOut(
        access_token=issued.token,
        expires_at=issued.expires_at,
        user=UserOut.model_validate(issued.user),
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: SessionPrincipalDep, container: ContainerDep) -> None:
    """Revoke the current session token."""
    await container.auth.logout(principal)


@router.get("/me", response_model=MeOut)
async def me(principal: PrincipalDep, container: ContainerDep) -> MeOut:
    """The authenticated user (session or API key)."""
    user = await container.auth.get_user(principal.user_id)
    return MeOut(user=UserOut.model_validate(user), credential=principal.credential.value)
