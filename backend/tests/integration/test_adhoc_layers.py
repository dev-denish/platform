"""DB-backed tests for ad-hoc layers (Wave 3: Added Layers) against a REAL
PostGIS database - the KPI/evolution exclusion, the project-scoped upload
RBAC re-check, and the is_adhoc-guarded soft-delete all need real
transaction/constraint semantics, so this is not faked (see
test_db_repositories.py for the same skip-guard convention this file follows).

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
from app.core.errors import ForbiddenError, NotFoundError  # noqa: E402
from app.domain.dtos import CurrentUser, IngestMetadata  # noqa: E402
from app.domain.enums import DatasetType, Role  # noqa: E402
from app.repositories.datasets import (  # noqa: E402
    DatasetRepository,
    KpiRepository,
    LayerRepository,
)
from app.repositories.memberships import ProjectMembershipRepository  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.adhoc_layer_service import AdhocLayerService  # noqa: E402
from app.services.ingestion.service import IngestionService  # noqa: E402
from app.services.project_service import ProjectService  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db, get_settings(), object())


@pytest.fixture
def ingestion_service(db) -> IngestionService:
    return IngestionService(db, get_settings(), object())


@pytest.fixture
def adhoc_layer_service(db) -> AdhocLayerService:
    return AdhocLayerService(db)


def _make_user(db: Database, role: Role) -> CurrentUser:
    username = f"adhoctest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", role.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=role)


def _make_project(db: Database, name: str) -> uuid.UUID:
    with db.transaction() as cur:
        project_id, _created = ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")
    return project_id


def _add_member(db: Database, project_id, user: CurrentUser, role: Role, added_by: CurrentUser) -> None:
    with db.transaction() as cur:
        ProjectMembershipRepository(cur).add(
            project_id=project_id, user_id=user.user_id, role=role, added_by=added_by.user_id
        )


def _insert_classified_layer(
    db: Database, *, project_id, date_processed: str, class_legend: dict, is_adhoc: bool = False
) -> tuple[uuid.UUID, uuid.UUID]:
    """A dataset + spatial_layer row shaped like IngestionService._ingest_raster
    would produce - dated and legend-bearing, i.e. eligible for Landscape
    Evolution (see compute_evolution's own eligibility rule)."""
    with db.transaction() as cur:
        dataset_id = DatasetRepository(cur).insert(
            project_id=project_id, dataset_type="LULC", source="S", accuracy_score=90.0,
            date_processed=date_processed, batch_id=uuid.uuid4(), is_adhoc=is_adhoc,
        )
        layer_id = LayerRepository(cur).insert(
            dataset_id=dataset_id, file_key="rasters/x.tif", preview_key="previews/x.png",
            crs="EPSG:4326", bounds=(0.0, 0.0, 1.0, 1.0), pixel_size_m=10.0,
            band_count=1, class_legend=class_legend,
        )
    return dataset_id, layer_id


# --------------------------------------------------------------- KPI exclusion


def test_kpi_repository_excludes_adhoc_datasets(db):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    _did, layer_id = _insert_classified_layer(
        db, project_id=pid, date_processed="2026-01-01",
        class_legend={"1": "Forest"}, is_adhoc=True,
    )
    with db.transaction() as cur:
        KpiRepository(cur).upsert(_did, "total_area", 500.0, "ha")

    with db.connection() as conn, conn.cursor() as cur:
        rows = KpiRepository(cur).for_project(pid)

    assert not any(str(r["layer_id"]) == str(layer_id) for r in rows)


# --------------------------------------------------------------- evolution


def test_evolution_totals_unchanged_after_adhoc_layer_added(db, project_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")

    _did1, layer1 = _insert_classified_layer(
        db, project_id=pid, date_processed="2026-01-01", class_legend={"1": "Forest", "2": "Water"}
    )
    _did2, layer2 = _insert_classified_layer(
        db, project_id=pid, date_processed="2026-06-01", class_legend={"1": "Forest", "2": "Water"}
    )
    with db.transaction() as cur:
        k = KpiRepository(cur)
        k.upsert(_did1, "class_area_forest", 100.0, "ha")
        k.upsert(_did1, "class_area_water", 20.0, "ha")
        k.upsert(_did2, "class_area_forest", 150.0, "ha")
        k.upsert(_did2, "class_area_water", 20.0, "ha")

    before = project_service.get_evolution(pid, admin)
    assert before.applicable is True
    assert before.dates == ["2026-01-01", "2026-06-01"]

    # Add an ad-hoc layer with its OWN dated, classified legend and KPIs - if
    # this leaked into the official comparison it would introduce a THIRD
    # date and corrupt every existing class row's before/after numbers.
    adhoc_did, _adhoc_layer = _insert_classified_layer(
        db, project_id=pid, date_processed="2026-03-01",
        class_legend={"1": "Forest", "2": "Water"}, is_adhoc=True,
    )
    with db.transaction() as cur:
        k = KpiRepository(cur)
        k.upsert(adhoc_did, "class_area_forest", 9999.0, "ha")
        k.upsert(adhoc_did, "class_area_water", 9999.0, "ha")

    after = project_service.get_evolution(pid, admin)

    assert after.model_dump() == before.model_dump()


# --------------------------------------------------------------- upload RBAC


def test_adhoc_upload_to_a_project_without_membership_is_denied(db, ingestion_service):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")  # exists, actor never added
    actor = _make_user(db, Role.GIS_ASSOCIATE)
    meta = IngestMetadata(
        project_id=pid, dataset_type=DatasetType.SATELLITE, source="Ad-hoc upload",
        date_processed="2026-01-01", is_adhoc=True,
    )

    with pytest.raises(NotFoundError), db.transaction() as cur:
        ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001


def test_adhoc_upload_with_non_gis_associate_project_role_is_forbidden(db, ingestion_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    actor = _make_user(db, Role.GIS_ASSOCIATE)  # global role passes UPLOAD_ROLES...
    _add_member(db, pid, actor, Role.ANALYST, added_by=admin)  # ...but project role doesn't
    meta = IngestMetadata(
        project_id=pid, dataset_type=DatasetType.SATELLITE, source="Ad-hoc upload",
        date_processed="2026-01-01", is_adhoc=True,
    )

    with pytest.raises(ForbiddenError), db.transaction() as cur:
        ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001


def test_adhoc_upload_with_project_level_gis_associate_role_succeeds(db, ingestion_service):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    actor = _make_user(db, Role.ANALYST)  # global role, irrelevant here
    _add_member(db, pid, actor, Role.GIS_ASSOCIATE, added_by=admin)  # project role IS
    meta = IngestMetadata(
        project_id=pid, dataset_type=DatasetType.SATELLITE, source="Ad-hoc upload",
        date_processed="2026-01-01", is_adhoc=True,
    )

    with db.transaction() as cur:
        resolved = ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001
    assert resolved == pid


# --------------------------------------------------------------- soft delete


def test_soft_delete_adhoc_is_guarded_by_the_is_adhoc_flag(db):
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    with db.transaction() as cur:
        formal_did = DatasetRepository(cur).insert(
            project_id=pid, dataset_type="LULC", source="S", accuracy_score=90.0,
            date_processed="2026-01-01", batch_id=uuid.uuid4(), is_adhoc=False,
        )
        adhoc_did = DatasetRepository(cur).insert(
            project_id=pid, dataset_type="Satellite / Raw Imagery", source="Quick raster",
            accuracy_score=None, date_processed="2026-01-01", batch_id=uuid.uuid4(), is_adhoc=True,
        )

    admin = _make_user(db, Role.ADMINISTRATOR)
    with db.transaction() as cur:
        # A formal (non-adhoc) dataset must never be reachable through this
        # guarded UPDATE, even by id.
        assert DatasetRepository(cur).soft_delete_adhoc(formal_did, deleted_by=admin.user_id) is False
        assert DatasetRepository(cur).soft_delete_adhoc(adhoc_did, deleted_by=admin.user_id) is True

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT deleted_at FROM dataset WHERE dataset_id = %s", (str(formal_did),))
        assert cur.fetchone()["deleted_at"] is None
        cur.execute("SELECT deleted_at, deleted_by FROM dataset WHERE dataset_id = %s", (str(adhoc_did),))
        row = cur.fetchone()
        assert row["deleted_at"] is not None
        assert str(row["deleted_by"]) == str(admin.user_id)


def test_adhoc_layer_service_remove_writes_an_audit_entry_and_requires_upload_role(
    db, adhoc_layer_service
):
    admin = _make_user(db, Role.ADMINISTRATOR)
    pid = _make_project(db, f"Proj-{uuid.uuid4()}")
    viewer_member = _make_user(db, Role.VIEWER)
    _add_member(db, pid, viewer_member, Role.VIEWER, added_by=admin)

    with db.transaction() as cur:
        dataset_id = DatasetRepository(cur).insert(
            project_id=pid, dataset_type="Satellite / Raw Imagery", source="Quick raster",
            accuracy_score=None, date_processed="2026-01-01", batch_id=uuid.uuid4(), is_adhoc=True,
        )
        layer_id = LayerRepository(cur).insert_non_raster(
            dataset_id=dataset_id, layer_kind="vector", crs="EPSG:4326",
            bounds=(0.0, 0.0, 1.0, 1.0),
        )

    # A project member without upload-level project role is blocked.
    with pytest.raises(ForbiddenError):
        adhoc_layer_service.remove(layer_id, viewer_member)

    # The Administrator who can upload can also remove it.
    adhoc_layer_service.remove(layer_id, admin)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT deleted_at FROM dataset WHERE dataset_id = %s", (str(dataset_id),))
        assert cur.fetchone()["deleted_at"] is not None
        cur.execute(
            "SELECT actor_id, action, target FROM audit_log "
            "WHERE action = 'delete_dataset' AND target = %s ORDER BY created_at DESC LIMIT 1",
            (str(dataset_id),),
        )
        row = cur.fetchone()
    assert row is not None
    assert str(row["actor_id"]) == str(admin.user_id)

    # Second removal: already gone -> NotFoundError, same convention as
    # ReferenceLayerService.remove.
    with pytest.raises(NotFoundError):
        adhoc_layer_service.remove(layer_id, admin)
