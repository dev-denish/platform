"""Auth endpoints (v1). Login accepts JSON now (typed LoginRequest) and returns an
access+refresh pair; /refresh rotates the access token; /logout revokes the
current session (Wave: session revocation); /me echoes the caller.
Login, refresh, and change-password (Wave: password reset) are all rate-limited
5/minute per client address via the shared `limiter`."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUserDep, get_auth_service, get_bearer_token
from app.core.ratelimit import limiter
from app.domain.dtos import (
    ChangePasswordRequest,
    CurrentUser,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenPair,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
@limiter.limit("5/minute")
def login(
    request: Request,
    body: LoginRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenPair:
    return auth.login(body.username, body.password)


@router.post("/refresh", response_model=TokenPair)
@limiter.limit("5/minute")
def refresh(
    request: Request,
    body: RefreshRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenPair:
    return auth.refresh(body.refresh_token)


@router.post("/logout", status_code=204, response_model=None)
def logout(
    body: LogoutRequest,
    token: Annotated[str, Depends(get_bearer_token)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    """Revokes the access token this call itself authenticates with, plus the
    refresh token in the body if the caller supplies one - so neither can be
    used again even though both would otherwise still pass signature/expiry
    checks. Not rate-limited like /login and /change-password: those guard a
    password-guessing surface, this doesn't (there's no secret to guess)."""
    auth.logout(token, body.refresh_token)


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUserDep) -> CurrentUser:
    return user


@router.post("/change-password", status_code=204, response_model=None)
@limiter.limit("5/minute")
def change_password(
    request: Request,
    body: ChangePasswordRequest,
    user: CurrentUserDep,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    """Wave: password reset, self-service side. Rate-limited the same as
    /login (5/minute, same IP-keyed limiter) - this form asks for a current
    password too, so it's an equally viable brute-force target for anyone
    at an unlocked, already-logged-in browser trying to guess it."""
    auth.change_password(user, body.current_password, body.new_password)
