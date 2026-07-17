"""Unit tests for password hashing and JWT issuance/verification, including the
access-vs-refresh type separation, tamper rejection, and expiry handling."""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import AuthError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def _settings(**over) -> Settings:
    base = dict(environment="test", jwt_secret="k" * 48, db_password="pw")
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong", h) is False


def test_verify_rejects_malformed_hash_without_raising():
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_access_token_roundtrip():
    s = _settings()
    tok = create_access_token(s, user_id="u1", username="alice", role="Administrator")
    claims = decode_token(s, tok, expected_type="access")
    assert claims["sub"] == "u1"
    assert claims["username"] == "alice"
    assert claims["role"] == "Administrator"
    assert claims["typ"] == "access"


def test_refresh_token_has_jti_and_type():
    s = _settings()
    tok = create_refresh_token(s, user_id="u1")
    claims = decode_token(s, tok, expected_type="refresh")
    assert claims["typ"] == "refresh"
    assert "jti" in claims


def test_access_token_rejected_when_refresh_expected():
    s = _settings()
    access = create_access_token(s, user_id="u1", username="a", role="Viewer")
    with pytest.raises(AuthError):
        decode_token(s, access, expected_type="refresh")


def test_token_signed_with_other_secret_is_rejected():
    s1 = _settings(jwt_secret="a" * 48)
    s2 = _settings(jwt_secret="b" * 48)
    tok = create_access_token(s1, user_id="u1", username="a", role="Viewer")
    with pytest.raises(AuthError):
        decode_token(s2, tok, expected_type="access")


def test_expired_token_is_rejected():
    s = _settings(access_token_ttl_minutes=-1)  # already expired on creation
    tok = create_access_token(s, user_id="u1", username="a", role="Viewer")
    with pytest.raises(AuthError):
        decode_token(s, tok, expected_type="access")
