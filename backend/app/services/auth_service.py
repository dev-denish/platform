"""Authentication service. Verifies credentials, issues an access/refresh token
pair, and refreshes. Raises domain AuthError (never HTTPException) so the transport
layer owns HTTP mapping. Login is audited with the real actor."""
from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import AuthError, ValidationError
from app.core.security import (
    _DUMMY_HASH,
    MIN_PASSWORD_LENGTH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.domain.dtos import CurrentUser, TokenPair
from app.domain.enums import AuditAction, Role
from app.repositories.audit import AuditRepository
from app.repositories.revoked_tokens import RevokedTokenRepository
from app.repositories.users import UserRepository


class AuthService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def login(self, username: str, password: str) -> TokenPair:
        with self.db.transaction() as cur:
            user = UserRepository(cur).get_by_username(username)
            # Genuinely constant work whether or not the user exists (avoid user
            # enumeration via timing): always run one real bcrypt compare, against
            # the found hash or else a fixed dummy one - `and` alone would
            # short-circuit past bcrypt entirely for a nonexistent username.
            password_matches = verify_password(
                password, user["password_hash"] if user else _DUMMY_HASH
            )
            ok = bool(user) and password_matches
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
            # A logged-out refresh token must not be able to mint a fresh, still-live
            # access token - otherwise revoking it on logout would be a no-op.
            if payload.get("jti") and RevokedTokenRepository(cur).is_revoked(payload["jti"]):
                raise AuthError("This session has been signed out; please sign in again.")
            user = UserRepository(cur).get_by_id(payload["sub"])
        if not user:
            raise AuthError("User no longer exists.")
        return self._issue(str(user["user_id"]), user["username"], user["role"])

    def change_password(
        self, user: CurrentUser, current_password: str, new_password: str
    ) -> None:
        """Self-service password change (Wave: password reset) - the
        inverse of `login`'s "verify a password" step: here the caller's
        identity is already established (a valid access token got them
        this far), so there's no anonymous-username timing side-channel to
        guard against with the `_DUMMY_HASH` trick `login` needs - we
        always have a REAL hash (their own) to compare against.

        Wrong current password -> the same AuthError `login` raises for bad
        credentials (401, clear rejection) - no password change happens."""
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        with self.db.transaction() as cur:
            repo = UserRepository(cur)
            row = repo.get_any_by_id(user.user_id)
            if row is None:
                raise AuthError("User no longer exists.")
            if not verify_password(current_password, row["password_hash"]):
                raise AuthError("Incorrect current password.")
            repo.update_password(user.user_id, hash_password(new_password))
            AuditRepository(cur).record(
                actor_id=user.user_id, actor_name=user.username,
                action=AuditAction.CHANGE_OWN_PASSWORD, target=str(user.user_id),
                detail=f"Changed own password for account '{user.username}'.",
            )

    def current_user_from_access(self, token: str) -> CurrentUser:
        payload = decode_token(self.settings, token, expected_type="access")
        jti = payload.get("jti")  # absent only on a token issued pre-upgrade; it
        # naturally expires within access_token_ttl_minutes, so there's nothing to
        # check it against - fail open rather than crash on a still-valid old token.
        if jti is not None:
            with self.db.connection() as conn, conn.cursor() as cur:
                if RevokedTokenRepository(cur).is_revoked(jti):
                    raise AuthError("This session has been signed out; please sign in again.")
        return CurrentUser(
            user_id=payload["sub"], username=payload["username"], role=Role(payload["role"])
        )

    def logout(self, access_token: str, refresh_token: str | None = None) -> None:
        """Revoke the caller's current session. The access token used to make this
        very call is always revoked (that's the one every subsequent request would
        otherwise still be able to use for up to access_token_ttl_minutes); the
        refresh token is revoked too when the caller supplies one, so it can't be
        used to mint a fresh, non-revoked access token right after logout. An
        invalid/already-expired refresh token is ignored rather than failing the
        whole logout - there's nothing left to revoke either way."""
        access_payload = decode_token(self.settings, access_token, expected_type="access")
        with self.db.transaction() as cur:
            repo = RevokedTokenRepository(cur)
            repo.revoke(
                jti=access_payload["jti"],
                user_id=access_payload["sub"],
                expires_at=datetime.fromtimestamp(access_payload["exp"], UTC),
            )
            if refresh_token:
                try:
                    refresh_payload = decode_token(
                        self.settings, refresh_token, expected_type="refresh"
                    )
                except AuthError:
                    pass
                else:
                    repo.revoke(
                        jti=refresh_payload["jti"],
                        user_id=refresh_payload["sub"],
                        expires_at=datetime.fromtimestamp(refresh_payload["exp"], UTC),
                    )

    def _issue(self, user_id: str, username: str, role: str) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(
                self.settings, user_id=user_id, username=username, role=role
            ),
            refresh_token=create_refresh_token(self.settings, user_id=user_id),
            expires_in=self.settings.access_token_ttl_minutes * 60,
        )
