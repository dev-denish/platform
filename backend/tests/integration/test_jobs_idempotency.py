"""DB-backed tests for the idempotency-key semantics in JobService/JobRepository
against a REAL PostGIS database - the advisory-lock + window logic needs real
transaction semantics, so this is not faked (see test_db_repositories.py for the
same skip-guard convention this file follows).

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
from app.repositories.jobs import JobRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.jobs_service import JobService  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def job_service(db) -> JobService:
    return JobService(db, get_settings())


def _make_user(db: Database) -> uuid.UUID:
    """`jobs.user_id` FKs to app_user - real rows keep this test honest about the
    actual schema instead of a bare random UUID that would only work by accident
    of there being no FK."""
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(f"jobtest-{uuid.uuid4()}", "x", "Administrator")
    return row["user_id"]


def test_same_key_within_window_returns_same_job_id(db, job_service):
    user_id = _make_user(db)
    key = f"idem-{uuid.uuid4()}"

    job_id_1, is_new_1 = job_service.submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=key, request_id=None
    )
    job_id_2, is_new_2 = job_service.submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=key, request_id=None
    )

    assert is_new_1 is True
    assert is_new_2 is False
    assert job_id_1 == job_id_2


def test_different_key_inserts_new_job(db, job_service):
    user_id = _make_user(db)

    job_id_1, _ = job_service.submit(
        user_id=user_id, kind="ingest_dataset",
        idempotency_key=f"idem-{uuid.uuid4()}", request_id=None,
    )
    job_id_2, is_new = job_service.submit(
        user_id=user_id, kind="ingest_dataset",
        idempotency_key=f"idem-{uuid.uuid4()}", request_id=None,
    )

    assert is_new is True
    assert job_id_1 != job_id_2


def test_no_idempotency_key_always_inserts_new_job(db, job_service):
    user_id = _make_user(db)

    job_id_1, is_new_1 = job_service.submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=None, request_id=None
    )
    job_id_2, is_new_2 = job_service.submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=None, request_id=None
    )

    assert is_new_1 is True
    assert is_new_2 is True
    assert job_id_1 != job_id_2


def test_different_users_same_key_do_not_collide(db, job_service):
    key = f"idem-{uuid.uuid4()}"
    user_a, user_b = _make_user(db), _make_user(db)

    job_id_a, is_new_a = job_service.submit(
        user_id=user_a, kind="ingest_dataset", idempotency_key=key, request_id=None
    )
    job_id_b, is_new_b = job_service.submit(
        user_id=user_b, kind="ingest_dataset", idempotency_key=key, request_id=None
    )

    assert is_new_a is True
    assert is_new_b is True
    assert job_id_a != job_id_b


def test_key_reused_past_window_inserts_new_job(db, job_service):
    """Simulates 'past window' by backdating submitted_at directly (there is no
    settings knob small enough to wait out a real window in a fast test) - this
    proves the SQL predicate, not the clock."""
    user_id = _make_user(db)
    key = f"idem-{uuid.uuid4()}"

    job_id_1, _ = job_service.submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=key, request_id=None
    )
    with db.transaction() as cur:
        cur.execute(
            "UPDATE jobs SET submitted_at = now() - interval '48 hours' WHERE id = %s",
            (str(job_id_1),),
        )
        # sanity: the repository sees it as stale for the default 24h window
        assert JobRepository(cur).find_recent(user_id, key, 24) is None

    job_id_2, is_new = job_service.submit(
        user_id=user_id, kind="ingest_dataset", idempotency_key=key, request_id=None
    )

    assert is_new is True
    assert job_id_2 != job_id_1
