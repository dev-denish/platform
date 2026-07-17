"""DB-backed integration tests for the repository layer against a REAL PostGIS
database. Skipped automatically unless DMRV_TEST_DATABASE is set (it is set in CI,
where a postgis service container is available, and by the docker-compose test
profile). This is what proves the SQL, indexes, constraints, and upsert semantics
actually behave - things a faked DB cannot verify.

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
from app.repositories.datasets import DatasetRepository, KpiRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


def test_find_or_create_by_name_is_idempotent(db):
    name = f"Proj-{uuid.uuid4()}"
    with db.transaction() as cur:
        pid1 = ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")
    with db.transaction() as cur:
        pid2 = ProjectRepository(cur).find_or_create_by_name(name.upper(), "Karnataka")
    assert pid1 == pid2  # case-insensitive unique index prevents a duplicate


def test_kpi_upsert_does_not_duplicate(db):
    name = f"Proj-{uuid.uuid4()}"
    with db.transaction() as cur:
        pid = ProjectRepository(cur).find_or_create_by_name(name, "R")
        did = DatasetRepository(cur).insert(
            project_id=pid, dataset_type="LULC", source="S", accuracy_score=90.0,
            date_processed="2026-01-01", batch_id=uuid.uuid4(),
        )
        k = KpiRepository(cur)
        k.upsert(did, "total_area", 100.0, "ha")
        k.upsert(did, "total_area", 250.0, "ha")  # same key -> update, not insert
    with db.connection() as conn, conn.cursor() as cur:
        rows = KpiRepository(cur).for_project(pid)
    totals = [r for r in rows if r["metric_name"] == "total_area"]
    assert len(totals) == 1
    assert float(totals[0]["value"]) == 250.0
