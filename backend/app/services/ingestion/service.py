"""
Ingestion orchestration.

Existing implementation (MVP): the async endpoint called a fully synchronous
`ingest_raster` inline on the event loop; files were written to disk BEFORE the DB
commit (orphans on failure); the DB writes were one transaction but the filesystem
side effects were not rolled back; and the audit row recorded no real user.

Enterprise solution: a synchronous, side-effect-ordered orchestration designed to
run OFF the request path (in a worker / threadpool). It:
  1. computes stats + preview + reprojection on staged input (bounded memory),
  2. writes ALL database rows in ONE transaction (project, dataset, layer, KPIs,
     audit) - attributed to the real actor,
  3. only AFTER the commit succeeds promotes artifacts into durable storage, so a
     failed transaction leaves no dangling objects.
The heavy raster work is pure/synchronous by design so the caller decides how to run
it (thread pool now; distributed workers later) - see workers/queue.py.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import uuid
from uuid import UUID

import rasterio

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import UnprocessableError
from app.domain.dtos import BandStatsOut, CurrentUser, IngestMetadata, IngestResult
from app.domain.enums import AuditAction
from app.repositories.audit import AuditRepository
from app.repositories.datasets import DatasetRepository, KpiRepository, LayerRepository
from app.repositories.projects import ProjectRepository
from app.services.ingestion import raster as R
from app.services.ingestion.storage import Storage


class IngestionService:
    def __init__(self, db: Database, settings: Settings, storage: Storage) -> None:
        self.db = db
        self.settings = settings
        self.storage = storage

    def ingest(
        self, *, staged_path: str, meta: IngestMetadata, legend: R.Legend, actor: CurrentUser
    ) -> IngestResult:
        dataset_id = uuid.uuid4()
        batch_id = uuid.uuid4()
        work_dir = tempfile.mkdtemp(prefix="dmrv_ingest_")
        reproj_tmp = os.path.join(work_dir, f"{dataset_id}.tif")
        preview_tmp = os.path.join(work_dir, f"{dataset_id}.png")

        try:
            # ---- 1. heavy, bounded raster work (safe to run in a worker/threadpool)
            try:
                src_crs, bounds = R.reproject_to_4326(
                    staged_path, reproj_tmp, block=self.settings.raster_window_size
                )
                # Cheap header-only read (no pixel data) - the real band count
                # this layer's tiles/symbology controls key off of, rather than
                # something the frontend would have to guess or hardcode.
                with rasterio.open(reproj_tmp) as reproj_src:
                    band_count = reproj_src.count
                R.render_preview(reproj_tmp, preview_tmp, legend)
                stats = R.compute_stats(
                    staged_path, legend, block=self.settings.raster_window_size
                )
            except Exception as e:  # rasterio/GDAL failure -> client-safe 422
                raise UnprocessableError(f"Raster could not be processed: {e}") from e

            raster_key = f"rasters/{dataset_id}.tif"
            preview_key = f"previews/{dataset_id}.png"

            # ---- 2. one transaction for ALL rows, attributed to the real actor
            with self.db.transaction() as cur:
                project_id: UUID = ProjectRepository(cur).find_or_create_by_name(
                    meta.project_name, meta.region
                )
                DatasetRepository(cur).insert(
                    project_id=project_id, dataset_type=meta.dataset_type.value,
                    source=meta.source, accuracy_score=meta.accuracy_score,
                    date_processed=meta.date_processed.isoformat(), batch_id=batch_id,
                )
                # dataset_id was DB-generated above; re-read is avoided by using our
                # own uuid for artifacts and letting the DB own the dataset PK. To keep
                # artifact keys and the row aligned we fetch the generated id:
                cur.execute(
                    "SELECT dataset_id FROM dataset WHERE batch_id = %s "
                    "ORDER BY loaded_at DESC LIMIT 1",
                    (str(batch_id),),
                )
                dataset_id = cur.fetchone()["dataset_id"]  # type: ignore[index]
                raster_key = f"rasters/{dataset_id}.tif"
                preview_key = f"previews/{dataset_id}.png"

                LayerRepository(cur).insert(
                    dataset_id=dataset_id, file_key=raster_key, preview_key=preview_key,
                    crs="EPSG:4326", bounds=bounds, pixel_size_m=meta.pixel_size_m,
                    band_count=band_count, class_legend=legend,
                )
                kpis = KpiRepository(cur)
                kpis.upsert(dataset_id, "total_area", stats.total_area_ha, "ha")
                # band_stats (no legend) describes brightness/reflectance, not a
                # carbon-relevant class - nothing meaningful to KPI on there.
                if stats.class_area_ha is not None:
                    for label, area in stats.class_area_ha.items():
                        kpis.upsert(dataset_id, R.metric_key(label), area, "ha")
                AuditRepository(cur).record(
                    actor_id=actor.user_id, actor_name=actor.username,
                    action=AuditAction.INGEST_DATASET, target=str(dataset_id),
                    detail=(
                        f"{meta.project_name} ({meta.dataset_type.value}); "
                        f"source CRS {src_crs}; area measured in {stats.area_crs}; "
                        f"batch {batch_id}"
                    ),
                )

            # ---- 3. promote artifacts ONLY after commit (no orphans on failure)
            try:
                self.storage.save(preview_key, preview_tmp)
                self.storage.save(raster_key, reproj_tmp)
            except Exception:
                # The transaction above already committed and can't be rolled
                # back from here - a raster_key/preview_key that doesn't
                # actually exist in storage would otherwise surface forever
                # (broken preview, 404ing tiles) on every read path. Soft-
                # delete through the same `deleted_at` column every one of
                # those read paths already filters on, so the phantom row
                # disappears instead. A retry (see workers/jobs.py) creates a
                # fresh dataset row rather than resurrecting this one -
                # consistent with ingestion already being documented as not
                # idempotent across a full re-run.
                with self.db.transaction() as cur:
                    DatasetRepository(cur).mark_failed_promotion(dataset_id)
                raise

            band_stats = (
                BandStatsOut(
                    min=stats.band_stats.min, max=stats.band_stats.max,
                    mean=stats.band_stats.mean, stddev=stats.band_stats.stddev,
                )
                if stats.band_stats is not None
                else None
            )
            return IngestResult(
                project_id=project_id, dataset_id=dataset_id, batch_id=batch_id,
                total_area_ha=stats.total_area_ha, class_stats=stats.class_area_ha,
                band_stats=band_stats,
            )
        finally:
            # staged_path is NOT cleaned up here - see workers/run_ingest_job,
            # which owns deleting it, and only once the job reaches a truly
            # terminal state. This function's own exceptions (from step 1)
            # are always wrapped as UnprocessableError (non-retryable), but a
            # bare exception from step 2/3 is retried by the worker with this
            # SAME staged_path (see workers/jobs.py's Retry) - deleting it
            # unconditionally here made every such retry fail on a missing
            # file, which then got misreported as "raster could not be
            # processed" instead of the real, transient cause.
            for p in (reproj_tmp, preview_tmp):
                with contextlib.suppress(OSError):
                    if os.path.exists(p):
                        os.unlink(p)
            with contextlib.suppress(OSError):
                os.rmdir(work_dir)
