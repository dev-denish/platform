"""DB-backed tests for session revocation (Wave: session revocation) against a
REAL PostGIS database - `revoked_token` is a real table with a real FK/PK, and
the whole point being proven here is that a write to it on /auth/logout is
actually enforced on the very next request, not just recorded and ignored. Same
skip-guard convention as test_user_management.py.

Run locally with, e.g.:
    DMRV_TEST_DATABASE=1 DMRV_DB_HOST=localhost DMRV_DB_USER=dmrv \
    DMRV_DB_PASSWORD=... DMRV_DB_NAME=dmrv_test pytest -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

if not os.getenv("DMRV_TEST_DATABASE"):
    pytest.skip("DMRV_TEST_DATABASE not set; skipping DB integration tests", allow_module_level=True)

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core.errors import AuthError  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def auth_service(db) -> AuthService:
    return AuthService(db, get_settings())


@pytest.fixture
def logged_in_user(db, auth_service):
    """A real account, logged in through the real AuthService.login - the
    returned TokenPair carries a genuine, DB-issued jti on both tokens."""
    username = f"revoke-{uuid.uuid4()}"
    password = "irrelevant123"  # noqa: S105 - test fixture, not a credential
    with db.transaction() as cur:
        UserRepository(cur).upsert(username, hash_password(password), Role.VIEWER.value)
    return auth_service.login(username, password)


def test_non_revoked_token_still_works(auth_service, logged_in_user):
    user = auth_service.current_user_from_access(logged_in_user.access_token)
    assert user.username.startswith("revoke-")


def test_revoked_access_token_is_rejected_on_subsequent_use(auth_service, logged_in_user):
    # Proves the same call succeeds before logout and only starts failing after -
    # not just that a fresh independent lookup happens to reject something.
    auth_service.current_user_from_access(logged_in_user.access_token)

    auth_service.logout(logged_in_user.access_token)

    with pytest.raises(AuthError):
        auth_service.current_user_from_access(logged_in_user.access_token)


def test_logout_revokes_the_refresh_token_too(auth_service, logged_in_user):
    auth_service.refresh(logged_in_user.refresh_token)  # still valid pre-logout

    auth_service.logout(logged_in_user.access_token, logged_in_user.refresh_token)

    with pytest.raises(AuthError):
        auth_service.refresh(logged_in_user.refresh_token)


def test_logout_is_idempotent(auth_service, logged_in_user):
    auth_service.logout(logged_in_user.access_token, logged_in_user.refresh_token)
    auth_service.logout(logged_in_user.access_token, logged_in_user.refresh_token)
