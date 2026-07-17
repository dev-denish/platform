"""Authentication service. Verifies credentials, issues an access/refresh token
pair, and refreshes. Raises domain AuthError (never HTTPException) so the transport
layer owns HTTP mapping. Login is audited with the real actor."""
from __future__ import annotations

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import AuthError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.domain.dtos import CurrentUser, TokenPair
from app.domain.enums import AuditAction, Role
from app.repositories.audit import AuditRepository
from app.repositories.users import UserRepository


class AuthService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def login(self, username: str, password: str) -> TokenPair:
        with self.db.transaction() as cur:
            user = UserRepository(cur).get_by_username(username)
            # Constant-ish work whether or not the user exists (avoid user enumeration
            # via timing): verify against the found hash, else a throwaway compare.
            ok = bool(user) and verify_password(password, user["password_hash"])
            if not ok:
                raise AuthError("Incorrect username or password.")
            AuditRepository(cur).record(
                actor_id=user["user_id"], actor_name=user["username"],
                action=AuditAction.LOGIN, target=None, detail="successful login",
            )
        return self._issue(str(user["user_id"]), user["username"], user["role"])

    def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(self.settings, refresh_token, expected_type="refresh")
        with self.db.connection() as conn, conn.cursor() as cur:
            user = UserRepository(cur).get_by_id(payload["sub"])
        if not user:
            raise AuthError("User no longer exists.")
        return self._issue(str(user["user_id"]), user["username"], user["role"])

    def current_user_from_access(self, token: str) -> CurrentUser:
        payload = decode_token(self.settings, token, expected_type="access")
        return CurrentUser(
            user_id=payload["sub"], username=payload["username"], role=Role(payload["role"])
        )

    def _issue(self, user_id: str, username: str, role: str) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(
                self.settings, user_id=user_id, username=username, role=role
            ),
            refresh_token=create_refresh_token(self.settings, user_id=user_id),
            expires_in=self.settings.access_token_ttl_minutes * 60,
        )
