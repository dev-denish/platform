"""DB-backed tests for the upload project-name footgun fix (memory:
wave_upload_project_name_footgun) against a REAL PostGIS database - the
whole point being proven here is the atomic find-or-create/exact-match
behavior against real unique-index constraints, so this is not faked (see
test_project_membership.py for the same skip-guard convention this file
follows).

Bug (confirmed live against the ephemeral dmrv-qa stack before this fix):
uploading with a project_name that doesn't exactly match an existing
project (e.g. "RA_GHG" vs the real "RA GHG") silently created a second,
empty-looking duplicate project instead of erroring.

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
from app.core.errors import NotFoundError  # noqa: E402
from app.domain.dtos import CurrentUser, IngestMetadata  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.repositories.projects import ProjectRepository  # noqa: E402
from app.repositories.users import UserRepository  # noqa: E402
from app.services.ingestion.service import IngestionService  # noqa: E402


@pytest.fixture(scope="module")
def db() -> Database:
    d = Database(get_settings())
    d.connect()
    yield d
    d.close()


@pytest.fixture
def ingestion_service(db) -> IngestionService:
    return IngestionService(db, get_settings(), object())


def _make_user(db: Database, role: Role) -> CurrentUser:
    username = f"footguntest-{uuid.uuid4()}"
    with db.transaction() as cur:
        row = UserRepository(cur).upsert(username, "x", role.value)
    return CurrentUser(user_id=row["user_id"], username=username, role=role)


def _make_project(db: Database, name: str) -> uuid.UUID:
    with db.transaction() as cur:
        project_id, _created = ProjectRepository(cur).find_or_create_by_name(name, "Karnataka")
    return project_id


def _meta(name: str, *, create_new_project: bool) -> IngestMetadata:
    return IngestMetadata(
        project_name=name, region="Karnataka", dataset_type="LULC", source="test",
        classification_method="", accuracy_score=None, date_processed="2026-01-01",
        pixel_size_m=10.0, create_new_project=create_new_project,
    )


def test_mismatched_name_is_rejected_instead_of_forking_a_duplicate(db, ingestion_service):
    """The exact reported bug, reproduced at the service layer: a project
    already exists ("RA GHG"), the caller typos it ("RA_GHG") and does NOT
    confirm this is a new project - this must error, and no second project
    may exist afterward."""
    real_name = f"RA GHG {uuid.uuid4()}"
    _make_project(db, real_name)
    typo_name = real_name.replace(" ", "_")
    actor = _make_user(db, Role.ADMINISTRATOR)
    meta = _meta(typo_name, create_new_project=False)

    with pytest.raises(NotFoundError), db.transaction() as cur:
        ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001

    with db.connection() as conn, conn.cursor() as cur:
        assert ProjectRepository(cur).get_by_name(typo_name) is None


def test_name_that_matches_no_project_at_all_is_rejected(db, ingestion_service):
    """Not just typos of an existing project - a wholly novel name, without
    explicit confirmation, must also error rather than silently create."""
    actor = _make_user(db, Role.ADMINISTRATOR)
    meta = _meta(f"Never Existed {uuid.uuid4()}", create_new_project=False)

    with pytest.raises(NotFoundError), db.transaction() as cur:
        ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001


def test_matching_name_reupload_still_succeeds_without_confirmation(db, ingestion_service):
    """The regression risk this fix must NOT introduce: the common, valid
    case of re-uploading to the SAME correctly-named project, with
    create_new_project left at its strict default (False), must keep
    working exactly as before - reusing the existing project, not erroring."""
    name = f"RA GHG {uuid.uuid4()}"
    existing_id = _make_project(db, name)
    actor = _make_user(db, Role.ADMINISTRATOR)
    meta = _meta(name, create_new_project=False)

    with db.transaction() as cur:
        project_id = ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001

    assert project_id == existing_id


def test_matching_is_case_insensitive_like_the_rest_of_the_app(db, ingestion_service):
    """Matches ProjectRepository.find_or_create_by_name's own unique index
    (`lower(name)`) - the convention already established elsewhere in this
    codebase, not a new rule invented for this fix."""
    name = f"Ra Ghg {uuid.uuid4()}"
    existing_id = _make_project(db, name)
    actor = _make_user(db, Role.ADMINISTRATOR)
    meta = _meta(name.upper(), create_new_project=False)

    with db.transaction() as cur:
        project_id = ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001

    assert project_id == existing_id


def test_explicit_create_new_project_still_creates_a_brand_new_project(db, ingestion_service):
    """The escape hatch: a genuinely new project must still be creatable by
    explicitly confirming it (create_new_project=True) - this fix must not
    remove the only way projects get created in this app."""
    actor = _make_user(db, Role.GIS_ASSOCIATE)
    name = f"Genuinely New {uuid.uuid4()}"
    meta = _meta(name, create_new_project=True)

    with db.transaction() as cur:
        project_id = ingestion_service._resolve_project(cur, meta, actor)  # noqa: SLF001

    with db.connection() as conn, conn.cursor() as cur:
        row = ProjectRepository(cur).get_by_name(name)
    assert row is not None
    assert row["project_id"] == project_id
