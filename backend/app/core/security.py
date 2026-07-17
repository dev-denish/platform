"""
Security primitives: password hashing + JWT access/refresh tokens.

Existing implementation (MVP): bcrypt (correct) + a single 8-hour JWT whose secret
defaulted to a known string, with no refresh, no token type, no issuer/audience,
and no revocation story. The frontend then stored it in localStorage.

Why insufficient: a known default secret is a full auth bypass; an 8-hour
non-refreshable access token means a stolen token is valid for 8 hours with no way
to shorten exposure. No `typ` claim means an access token and a refresh token are
interchangeable.

Enterprise solution:
  * short-lived ACCESS tokens (minutes) + long-lived REFRESH tokens (days),
  * a `typ` claim so the two cannot be confused,
  * issuer + audience validation,
  * a `jti` on refresh tokens so they can be added to a revocation list (table
    scaffolded in the migration; wired in a later phase),
  * secret comes only from validated Settings (no default in prod).
The transport fix (httpOnly Secure SameSite cookies instead of localStorage) is in
the frontend workstream; this module supports both by returning raw tokens.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import Settings
from app.core.errors import AuthError

TokenType = Literal["access", "refresh"]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        # Malformed hash in the DB -> treat as non-match, never 500.
        return False


def _encode(settings: Settings, claims: dict[str, Any], ttl: timedelta, typ: TokenType) -> str:
    now = datetime.now(UTC)
    payload = {
        **claims,
        "typ": typ,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    if typ == "refresh":
        payload["jti"] = str(uuid.uuid4())
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(settings: Settings, *, user_id: str, username: str, role: str) -> str:
    return _encode(
        settings,
        {"sub": user_id, "username": username, "role": role},
        timedelta(minutes=settings.access_token_ttl_minutes),
        "access",
    )


def create_refresh_token(settings: Settings, *, user_id: str) -> str:
    return _encode(
        settings,
        {"sub": user_id},
        timedelta(days=settings.refresh_token_ttl_days),
        "refresh",
    )


def decode_token(settings: Settings, token: str, *, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthError("Session expired; please sign in again.") from e
    except jwt.InvalidTokenError as e:
        raise AuthError("Invalid authentication token.") from e

    if payload.get("typ") != expected_type:
        raise AuthError("Wrong token type for this operation.")
    return payload
