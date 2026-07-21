"""Auth endpoints (v1). Login accepts JSON now (typed LoginRequest) and returns an
access+refresh pair; /refresh rotates the access token; /me echoes the caller.
Login and refresh are rate-limited at the app layer (see main.py)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import CurrentUserDep, get_auth_service
from app.core.ratelimit import limiter
from app.domain.dtos import CurrentUser, LoginRequest, RefreshRequest, TokenPair
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


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUserDep) -> CurrentUser:
    return user
