"""VNV Pipeline analysis compute service (Wave: VNV Pipeline NDFI go-live).

The self-hosted-compute counterpart to `GEEAnalysisService`, for catalog
entries whose `compute_source == "vnv_pipeline"` - today that is exactly
ONE entry, `vnv_ndfi` (Experimental, pending domain review - see
app/domain/analysis_catalog.py's own entry). Deliberately does NOT touch
GEE's existing analysis path/logic in any way.

Reuses `app.services.analysis_shared.prepare_analysis_refresh` for the
EXACT same catalog/permission/boundary validation `GEEAnalysisService`
already does - not a second, slightly-different copy - then inserts a
queued `analysis_runs` row (see migrations/versions/0019_analysis_runs.py
for why this is a separate table from the generic `jobs` row: it tracks
THIS compute path's own input/output raster refs and per-attempt failure
detail) and hands the real work off to
`app.workers.vnv_analysis_jobs.run_vnv_ndfi_analysis`.

Mirrors `GEEAnalysisService.enqueue_refresh`'s own job-queue dispatch
exactly (validate synchronously first, submit a `jobs` row, then hand off
to the worker without awaiting it, recovering identically on an enqueue
failure) so `POST /projects/{id}/analyses/{id}/refresh`'s existing 202 +
`JobAccepted(job_id, status_url)` response, and the frontend's existing
poll-then-refetch flow (`GET /jobs/{id}` then `GET /projects/{id}/
analyses/{id}`), work completely unchanged for this compute source too.
"""
from __future__ import annotations

import uuid
from uuid import UUID

from app.core.db import Database
from app.core.errors import DomainError
from app.domain.dtos import CurrentUser
from app.repositories.analysis_runs import AnalysisRunRepository
from app.services.analysis_shared import prepare_analysis_refresh
from app.services.jobs_service import JobService
from app.workers.queue import TaskRunner


class VNVAnalysisService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def enqueue_refresh(
        self,
        project_id: UUID,
        analysis_id: str,
        actor: CurrentUser,
        jobs: JobService,
        runner: TaskRunner,
    ) -> UUID:
        """Validates via the shared helper (same catalog/permission/
        boundary checks the GEE path uses), inserts a queued `analysis_runs`
        row, submits a generic `jobs` row, then hands off to the arq worker.
        `canopy_cover_pct`/`resolved_params` from the shared validation are
        intentionally discarded - the sidecar's spectral unmixing has no
        forest-definition-threshold concept, and `vnv_ndfi` declares no
        `config` (nothing to resolve), unlike the GEE analyses that use
        both."""
        _entry, boundary_geojson, _canopy_cover_pct, _resolved_params = prepare_analysis_refresh(
            self.db, project_id, analysis_id, actor
        )

        run_id = uuid.uuid4()
        with self.db.transaction() as cur:
            AnalysisRunRepository(cur).insert(
                run_id=run_id, project_id=project_id, analysis_type=analysis_id,
                status="queued",
            )

        # Deferred import: app.workers.vnv_analysis_jobs pulls in rasterio/
        # httpx-calling code that has no business loading onto the API
        # process's import path until a job actually needs to run - the
        # same "keep the worker module out of the request path" reasoning
        # GEEAnalysisService.enqueue_refresh's own deferred import documents
        # (there it's also circularity-avoidance; here the module doesn't
        # import back from this one, but the deferred-import convention is
        # kept for consistency and the same import-cost reasoning).
        from app.workers.vnv_analysis_jobs import run_vnv_ndfi_analysis

        job_id, is_new = jobs.submit(
            user_id=actor.user_id, kind="compute_vnv_ndfi",
            idempotency_key=None, request_id=None,
        )
        if is_new:
            try:
                await runner.run(
                    run_vnv_ndfi_analysis,
                    job_id=str(job_id), analysis_run_id=str(run_id), project_id=str(project_id),
                    boundary_geojson=boundary_geojson, actor=actor.model_dump(mode="json"),
                )
            except Exception as e:
                # The jobs-row insert AND the analysis_runs-row insert above
                # already committed - if enqueueing then fails (e.g. Redis
                # unreachable), mark BOTH failed instead of leaving either an
                # orphaned `queued` jobs row or a `queued` analysis_runs row
                # nothing will ever process. Same recovery
                # GEEAnalysisService.enqueue_refresh does for the `jobs` row
                # alone; this compute source additionally owns the
                # analysis_runs row's terminal state.
                jobs.mark_enqueue_failed(job_id, str(e))
                with self.db.transaction() as cur:
                    AnalysisRunRepository(cur).mark_failed(run_id, str(e))
                raise DomainError(
                    "Failed to enqueue this analysis. Please retry."
                ) from e
        return job_id
