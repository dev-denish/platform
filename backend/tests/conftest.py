"""Shared test fixtures.

Two tiers of tests:
  * unit - pure logic (raster math, security, config, pagination), no I/O.
  * API-contract - drive the real FastAPI app (routing, auth dependency, RBAC,
    response models, exception handlers, pagination) with the SERVICE layer faked,
    so they run WITHOUT Postgres. True DB-backed repository tests are marked
    `integration` and run against the PostGIS service in CI.
"""
from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import morecantile
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

# Make the `app` package importable when pytest is invoked from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.errors import AuthError, NotFoundError  # noqa: E402
from app.domain.dtos import (  # noqa: E402
    CurrentUser,
    DatasetOut,
    IngestResult,
    JobOut,
    KpiValue,
    LayerOut,
    Page,
    ProjectDetail,
    ProjectKpis,
    ProjectLayers,
    ProjectSummary,
)
from app.domain.enums import DatasetType, ProjectStatus, Role  # noqa: E402
from app.services.ingestion.cog import convert_to_cog  # noqa: E402

_ADMIN_ID = UUID("11111111-1111-1111-1111-111111111111")
_VIEWER_ID = UUID("22222222-2222-2222-2222-222222222222")
_PROJECT_ID = UUID("33333333-3333-3333-3333-333333333333")
_LAYER_ID = UUID("55555555-5555-5555-5555-555555555555")  # matches FakeProjectService's layer


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        environment="test",
        jwt_secret="x" * 48,
        db_password="test-pw",
        cors_allow_origins=["http://localhost:5173"],
        storage_backend="local",
        local_data_dir="/tmp/dmrv-test-data",
        upload_staging_dir="/tmp/dmrv-test-staging",
    )


class RealCog:
    """A genuine COG on disk (built via the real rio-cogeo code path, not mocked
    tile data) plus a z/x/y known to intersect it and one known NOT to, for the
    API-contract tile tests."""

    def __init__(self, path: str, z: int, x: int, y: int) -> None:
        self.path = path
        self.z, self.x, self.y = z, x, y
        self.out_of_bounds = (z, x + 1000, y + 1000)


@pytest.fixture
def real_cog(tmp_path) -> RealCog:
    h = w = 512
    arr = np.zeros((h, w), dtype="uint8")
    arr[: h // 2, :] = 1
    arr[h // 2 :, :] = 2
    src = tmp_path / "src.tif"
    profile = dict(
        driver="GTiff", height=h, width=w, count=1, dtype="uint8",
        crs="EPSG:4326", transform=from_origin(76.29, 13.07, 0.0002, 0.0002), nodata=0,
    )
    with rasterio.open(src, "w", **profile) as d:
        d.write(arr, 1)
    dst = tmp_path / "cog.tif"
    convert_to_cog(str(src), str(dst))

    with rasterio.open(dst) as d:
        bounds = d.bounds
    cx, cy = (bounds.left + bounds.right) / 2, (bounds.bottom + bounds.top) / 2
    tms = morecantile.tms.get("WebMercatorQuad")
    t = tms.tile(cx, cy, 14)
    return RealCog(str(dst), t.z, t.x, t.y)


# --------------------------------------------------------------- fakes


class FakeAuthService:
    def current_user_from_access(self, token: str) -> CurrentUser:
        if token == "admin-token":
            return CurrentUser(user_id=_ADMIN_ID, username="admin", role=Role.ADMINISTRATOR)
        if token == "viewer-token":
            return CurrentUser(user_id=_VIEWER_ID, username="viewer", role=Role.VIEWER)
        raise AuthError("Invalid authentication token.")


class FakeProjectService:
    """One instance is shared (via closure, see the `client` fixture) across every
    request in a test, so `_deleted` state persists request-to-request - needed to
    exercise "a second delete of the same project 404s" without a real DB."""

    def __init__(self) -> None:
        self._deleted: set[UUID] = set()

    def list_projects(self, limit: int, offset: int) -> Page[ProjectSummary]:
        if _PROJECT_ID in self._deleted:
            return Page[ProjectSummary](items=[], total=0, limit=limit, offset=offset)
        item = ProjectSummary(
            project_id=_PROJECT_ID, name="Karnataka Restoration", region="Karnataka",
            status=ProjectStatus.ACTIVE, latest_dataset_id=None,
            latest_accuracy=88.5, latest_processed=None,
        )
        return Page[ProjectSummary](items=[item], total=1, limit=limit, offset=offset)

    def get_project(self, project_id: UUID) -> ProjectDetail:
        if project_id != _PROJECT_ID or project_id in self._deleted:
            raise NotFoundError("Project not found.")
        return ProjectDetail(
            project_id=_PROJECT_ID, name="Karnataka Restoration", region="Karnataka",
            status=ProjectStatus.ACTIVE, start_date=None,
            datasets=[
                DatasetOut(
                    dataset_id=UUID("44444444-4444-4444-4444-444444444444"),
                    type=DatasetType.LULC, source="Sentinel-2", accuracy_score=88.5,
                    date_processed=None, loaded_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
                )
            ],
        )

    def get_kpis(self, project_id: UUID) -> ProjectKpis:
        if project_id != _PROJECT_ID or project_id in self._deleted:
            raise NotFoundError("Project not found.")
        return ProjectKpis(project_id=_PROJECT_ID, kpis={"total_area": KpiValue(value=2250.0, unit="ha")})

    def get_layers(self, project_id: UUID) -> ProjectLayers:
        if project_id != _PROJECT_ID or project_id in self._deleted:
            raise NotFoundError("Project not found.")
        return ProjectLayers(
            project_id=_PROJECT_ID,
            layers=[
                LayerOut(
                    layer_id=_LAYER_ID,
                    type=DatasetType.LULC, crs="EPSG:4326",
                    bounds=[[13.02, 76.29], [13.07, 76.34]], pixel_size_m=10.0,
                    preview_url="/previews/x.png", date_processed="2026-01-01",
                )
            ],
        )

    def portfolio_summary(self) -> dict:
        if _PROJECT_ID in self._deleted:
            return {"portfolio": {}, "project_count": 0}
        return {"portfolio": {"total_area": 2250.0}, "project_count": 1}

    def delete_project(self, project_id: UUID, actor: CurrentUser) -> None:
        if project_id != _PROJECT_ID or project_id in self._deleted:
            raise NotFoundError("Project not found.")
        self._deleted.add(project_id)


class FakeIngestionService:
    async def ingest(self, **kwargs) -> IngestResult:  # noqa: D401
        return IngestResult(
            project_id=_PROJECT_ID,
            dataset_id=UUID("66666666-6666-6666-6666-666666666666"),
            batch_id=UUID("77777777-7777-7777-7777-777777777777"),
            total_area_ha=2250.0, class_stats={"Forest": 1200.0, "Cropland": 1050.0},
        )


class FakeJobService:
    """Good enough to drive submit -> poll -> succeeded without a real Postgres:
    an in-memory dict keyed by job_id, shared between the `get_job_service`
    override and the `FakeTaskRunner` (which completes jobs directly against it,
    since there's no real arq/DB to run `workers.jobs.run_ingest_job` against)."""

    def __init__(self) -> None:
        self._jobs: dict[UUID, dict] = {}

    def submit(self, *, user_id, kind, idempotency_key, request_id) -> tuple[UUID, bool]:
        job_id = uuid.uuid4()
        self._jobs[job_id] = {
            "id": job_id, "user_id": user_id, "kind": kind, "status": "queued",
            "submitted_at": datetime.now(UTC), "started_at": None,
            "finished_at": None, "result": None, "error": None,
        }
        return job_id, True

    def mark_enqueue_failed(self, job_id: UUID, message: str) -> None:
        row = self._jobs[job_id]
        row["status"] = "failed"
        row["error"] = {"code": "enqueue_failed", "message": message}

    def complete_ingest(self, job_id: str, result: dict) -> None:
        row = self._jobs[UUID(job_id)]
        row["status"] = "succeeded"
        row["started_at"] = datetime.now(UTC)
        row["finished_at"] = datetime.now(UTC)
        row["result"] = result

    def get_for_user(self, job_id: UUID, user_id: UUID) -> JobOut:
        row = self._jobs.get(job_id)
        if not row or row["user_id"] != user_id:
            raise NotFoundError("Job not found.")
        return JobOut(**row)


class FakeTaskRunner:
    """`run()` is fire-and-forget dispatch (see workers/queue.py's contract
    change), so no caller awaits its return value. For `run_ingest_job`
    specifically - the only job kind that exists - this fake completes it
    synchronously against the shared `FakeJobService` instead of touching a real
    DB/raster pipeline, so the API-contract tier still runs WITHOUT Postgres."""

    def __init__(self, job_service: FakeJobService | None = None) -> None:
        self._jobs = job_service

    async def run(self, fn, /, *args, **kwargs):
        if self._jobs is not None and getattr(fn, "__name__", "") == "run_ingest_job":
            result = IngestResult(
                project_id=_PROJECT_ID,
                dataset_id=UUID("66666666-6666-6666-6666-666666666666"),
                batch_id=UUID("77777777-7777-7777-7777-777777777777"),
                total_area_ha=2250.0, class_stats={"Forest": 1200.0, "Cropland": 1050.0},
            )
            self._jobs.complete_ingest(kwargs["job_id"], result.model_dump(mode="json"))
            return
        res = fn(*args, **kwargs)
        # tolerate a plain callable or an async one, for any other fake usage
        if hasattr(res, "__await__"):
            return await res
        return res

    def shutdown(self) -> None: ...


class FakeTileService:
    """API-contract tier, no Postgres - so the layer/cog_key lookup is faked -
    but token verification and rendering are the REAL code
    (app.core.security.decode_token, app.services.tile_renderer.render_tile
    against a genuine COG from the `real_cog` fixture), so this actually proves
    the auth-gate and the rendered bytes, not just routing."""

    def __init__(self, settings: Settings, cog_path: str, layer_id: UUID = _LAYER_ID) -> None:
        self._settings = settings
        self._cog_path = cog_path
        self._layer_id = layer_id

    def verify_token(self, layer_id: UUID, token: str) -> None:
        from app.core.security import decode_token

        payload = decode_token(self._settings, token, expected_type="tile")
        if payload.get("sub") != str(layer_id):
            raise AuthError("This tile token is not valid for this layer.")

    def get_cog_key(self, layer_id: UUID) -> str:
        if layer_id != self._layer_id:
            raise NotFoundError("No tiles available for this layer.")
        return "cogs/fake.tif"

    def get_render_context(self, layer_id: UUID) -> tuple[str, dict | None]:
        return self.get_cog_key(layer_id), None

    def render(
        self, cog_key: str, z: int, x: int, y: int, *,
        legend=None, bands=None, stretch=None, color_overrides=None,
    ) -> bytes:
        from rio_tiler.errors import TileOutsideBounds

        from app.services.tile_renderer import render_tile

        try:
            return render_tile(
                self._cog_path, z, x, y,
                legend=legend, bands=bands, stretch=stretch, color_overrides=color_overrides,
            )
        except TileOutsideBounds as e:
            # Same translation TileService.render does - a fake that skipped
            # this would let a real bug in that translation slip past this test.
            raise NotFoundError("No data at this tile.") from e


@pytest.fixture
def client(test_settings):
    """A TestClient over the real app with service deps overridden by fakes.
    The app is NOT run through its DB-opening lifespan; app.state is populated
    directly, so no Postgres/Redis is required."""
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.main import create_app

    app = create_app(test_settings)
    job_service = FakeJobService()
    project_service = FakeProjectService()

    app.state.settings = test_settings
    app.state.db = object()
    app.state.storage = object()
    app.state.task_runner = FakeTaskRunner(job_service)

    app.dependency_overrides[deps.get_auth_service] = lambda: FakeAuthService()
    app.dependency_overrides[deps.get_project_service] = lambda: project_service
    app.dependency_overrides[deps.get_ingestion_service] = lambda: FakeIngestionService()
    app.dependency_overrides[deps.get_task_runner] = lambda: FakeTaskRunner(job_service)
    app.dependency_overrides[deps.get_job_service] = lambda: job_service

    return TestClient(app, raise_server_exceptions=True)
