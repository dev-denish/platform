"""End-to-end idempotency test against the REAL compose stack (Postgres + Redis +
arq worker + API), not fakes. Submits an upload with an `Idempotency-Key` header,
polls `GET /jobs/{id}` until it reaches a terminal state, then resubmits the exact
same request + key and asserts the SAME job_id comes back.

Needs BOTH:
  * DMRV_E2E_BASE_URL - base URL of the running API (e.g. http://localhost:8001
    if the backend port is published, or reached via the compose network/frontend
    proxy), and
  * DMRV_TEST_DATABASE (+ the usual DMRV_DB_* / DMRV_JWT_SECRET) - direct DB access
    to seed a login user with UserRepository, the same way scripts/create_admin.py
    does, since there is no /auth/register endpoint.

Run against a stack started via `docker compose -f deploy/docker-compose.yml up`,
e.g.:
    DMRV_E2E_BASE_URL=http://localhost:8001 DMRV_TEST_DATABASE=1 \
    DMRV_DB_HOST=localhost DMRV_DB_USER=dmrv DMRV_DB_PASSWORD=... \
    DMRV_DB_NAME=dmrv DMRV_JWT_SECRET=... pytest -m integration \
    tests/integration/test_upload_idempotency_e2e.py
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

pytestmark = pytest.mark.integration

_BASE_URL = os.getenv("DMRV_E2E_BASE_URL")

if not _BASE_URL or not os.getenv("DMRV_TEST_DATABASE"):
    pytest.skip(
        "DMRV_E2E_BASE_URL and DMRV_TEST_DATABASE must both be set to run the "
        "real-stack idempotency test",
        allow_module_level=True,
    )

import httpx  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402

_TERMINAL = {"succeeded", "failed", "dead_letter"}


@pytest.fixture(scope="module")
def admin_credentials() -> tuple[str, str]:
    username = f"e2e-admin-{uuid.uuid4()}"
    password = "e2e-strong-password-123"
    db = Database(get_settings())
    db.connect()
    try:
        with db.transaction() as cur:
            UserRepository(cur).upsert(username, hash_password(password), "Administrator")
    finally:
        db.close()
    return username, password


def _poll_until_terminal(client: httpx.Client, job_id: str, *, timeout_s: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/jobs/{job_id}")
        r.raise_for_status()
        body = r.json()
        if body["status"] in _TERMINAL:
            return body
        time.sleep(1.0)
    raise TimeoutError(f"job {job_id} did not reach a terminal state within {timeout_s}s")


def test_upload_with_idempotency_key_returns_same_job_on_replay(admin_credentials):
    username, password = admin_credentials
    idempotency_key = f"e2e-{uuid.uuid4()}"

    with httpx.Client(base_url=_BASE_URL, timeout=30.0) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        files = {"file": ("scene.tif", b"fakebytes-not-a-real-raster", "image/tiff")}
        data = {
            "project_name": f"E2E {uuid.uuid4()}", "dataset_type": "LULC",
            "source": "Sentinel-2", "accuracy_score": "88.5",
            "date_processed": "2026-01-01",
        }
        headers = {"Idempotency-Key": idempotency_key}

        first = client.post("/api/v1/datasets/upload", files=files, data=data, headers=headers)
        assert first.status_code == 202, first.text
        job_id_1 = first.json()["job_id"]

        outcome = _poll_until_terminal(client, job_id_1)
        # a fake .tif body will fail raster parsing - that's fine, we're proving
        # idempotency, not raster correctness; any terminal state is acceptable here.
        assert outcome["status"] in _TERMINAL

        second = client.post("/api/v1/datasets/upload", files=files, data=data, headers=headers)
        assert second.status_code == 202, second.text
        job_id_2 = second.json()["job_id"]

        assert job_id_2 == job_id_1
