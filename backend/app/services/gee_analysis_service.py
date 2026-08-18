"""
GEE-backed analysis queries (Wave: GEE analysis registry).

Five analyses are wired to real GEE datasets here - simple, synchronous
dataset queries against a project's boundary, fast enough for a normal
request/response (no job queue). Every other catalog entry
(app/domain/analysis_catalog.py) is "in-development": no query function
exists for it, and refresh() rejects it rather than pretend to compute one.

Each query function builds its ENTIRE computation graph server-side and
calls .getInfo() exactly once (or, for the two annual analyses, once for
ALL years combined) - never inside a Python loop - matching the convention
already established in scripts/gee_phase1_agb_proxy.py.

Four independent classification schemes are used across these analyses
(Dynamic World's 9 classes, ESA WorldCover's 11, Esri/Impact Observatory's
~9, MODIS IGBP's 17) plus Hansen's separate tree-cover-percent scheme. They
are NOT directly comparable - a caller must never merge or diff class
breakdowns across two different analyses; each result stands alone, labeled
with which product it came from.

Known dataset limitations surfaced in `stats["note"]` where they'd
otherwise mislead a reader:
  - Hansen `gain` is a whole-period 2000-2012 figure; this dataset does not
    track gain after 2012 at all.
  - Hansen's baseline forest area is threshold-sensitive BY CONSTRUCTION -
    it uses the live forest-definition canopy-cover setting at compute
    time, so re-running after that setting changes is expected, not a bug.
  - MODIS is 500m resolution vs. 10m for the other land-cover products -
    coarse trend context only, not microlandscape-scale area.

Wave: vegetation indices (NDVI/EVI/SAVI/MNDWI/NBR) compute FRESH from raw
Sentinel-2 imagery rather than querying a pre-built dataset, so unlike the
five analyses above they need real band math and cloud-free compositing -
`_s2_reflectance_composite`/`_annual_index_series` are the ONE shared
utility all five call, not five copies of the same compositing logic.
Verified live before implementing: S2_SR_HARMONIZED bands are raw DN in the
thousands, not [0,1] reflectance - EVI/SAVI's additive constants (+1, +0.5)
are calibrated for reflectance and would be silently wrong without the
divide(10000) in `_s2_reflectance_composite`. Sentinel-2 only (2017-present)
- a real multi-year series (one composite+value PER YEAR, not a snapshot),
matching the trend-chart framing these were designed for. Landsat back to
2013 was evaluated and deliberately deferred: different band names/scale,
no Cloud Score+ equivalent (would need QA_PIXEL bit-mask instead), 30m vs
10m resolution, and a likely fake "trend break" at the sensor handoff year -
real complications, not a drop-in extension.

UNLIKE the first five (the sync land-cover/forest-change dataset queries
above), all five of these run as an async job (app/workers/gee_analysis_
jobs.py), not synchronously - decided from real measured timing, not a
guess: NDVI's isolated reduceRegion-only estimate looked like ~3.5-4s, but
the actual end-to-end call (same code path a real request uses) measured
5-59s across repeated runs against real boundaries, well past "a few
seconds". EVI/SAVI/MNDWI/NBR share NDVI's exact compositing/reduceRegion
path via `_annual_index_series` (only the final band-math formula differs,
a negligible marginal cost) and were spot-checked live rather than assumed:
same order of magnitude as NDVI, so the same async decision applies to all
five without re-measuring each one from a blank slate.
`refresh()` below is for "sync"-execution catalog entries only;
`enqueue_refresh()` is the "async" path, mirroring POST /datasets/upload's
own job-queue dispatch exactly (validate synchronously so a bad request
fails immediately, then hand the actual compute to the worker).

Wave: AOI clip. Every one of these query functions' VISUALIZED map tile is
now `.clip(boundary)`'d immediately before `.getMapId()` - previously only
the vegetation-index five did this (`_s2_reflectance_composite` clips the
underlying composite itself); the other five clipped their `reduceRegion`
stats geometry but rendered the full, unclipped global dataset as a map
tile. The clip is applied to the final visualized image, never to the raw
dataset image feeding `reduceRegion` - stats were already boundary-scoped
and are unaffected by this change.

Wave: raw-imagery browsing. `s2_browse`/`s1_browse`/`landsat_browse` (see
their own "raw imagery browsing" section near the bottom of this file) are
BROWSE-ONLY: a single scene or a same-sensor-family mosaic (Landsat 8+9,
whichever has the least-cloud scene - never blended into one image), no
cloud-masked multi-image compositing, no band-math/index formula, no
cross-sensor math. They deliberately do NOT feed `_INDEX_FORMULAS`/
`_annual_index_series` and must never be wired into the vegetation-index
compute path above - the Landsat-vs-Sentinel-2 cross-sensor concerns this
docstring documents (different band names, no Cloud Score+ equivalent, a
fake "trend break" at the sensor handoff year) apply only to COMPUTE (index
math across sensors over time), not to raw single-sensor tile display,
which has none of those failure modes. "Supported for compute" (Sentinel-2
only, the five indices above) and "available to browse" (all three
sensors) are two deliberately different, non-overlapping boundaries - keep
them that way if this file or the analysis catalog ever grows again.

Wave: GEE tile caching. `_compute_cached` wraps `_compute` (kept entirely
unchanged - the sole GEE-touching, `fake_compute`-monkeypatchable seam
existing tests already rely on) with a Redis get/compute/set, keyed and
TTL'd by `app/services/gee_tile_cache.py` (see that module's own docstring
for the full TTL design/reasoning). It sits strictly BETWEEN `refresh()`/
the async worker and `_compute()`, purely to avoid redundant GEE calls -
the `analysis_result` DB table remains the separate, durable, no-TTL,
manual-refresh-only per-project result store `get_result()` reads from and
always has; this cache is never read by `get_result()`. `_cache_get`/
`_cache_set` are their own small monkeypatchable seam (same convention as
`init_ee`/`_compute`) and fail OPEN (log + treat as a miss/no-op) on any
Redis error - caching must never be the reason a request fails."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import ee
import redis as redis_lib

from app.core.config import get_settings
from app.core.db import Database
from app.core.errors import DomainError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain import analysis_config
from app.domain.analysis_catalog import CATALOG, get_catalog_entry
from app.domain.authz import require_project_view
from app.domain.dtos import (
    AnalysisPointValue,
    AnalysisResultOut,
    CurrentUser,
    ProjectAnalysisCatalog,
    ProjectAnalysisSummary,
)
from app.domain.enums import AuditAction
from app.domain.gee_class_legends import (
    DYNAMIC_WORLD_LEGEND,
    ESA_WORLDCOVER_LEGEND,
    ESRI_LULC_LEGEND,
    MODIS_IGBP_LEGEND,
    legend_entries,
)
from app.repositories.analysis_results import AnalysisResultRepository
from app.repositories.audit import AuditRepository
from app.repositories.forest_definition import ForestDefinitionRepository
from app.services import gee_tile_cache
from app.services.analysis_shared import prepare_analysis_refresh
from app.services.gee_client import init_ee
from app.services.index_summary import summarize_index_result
from app.services.jobs_service import JobService
from app.workers.queue import TaskRunner

log = get_logger("dmrv.gee_analysis")

AOI_CRS = "EPSG:32643"  # UTM 43N, Karnataka -- same convention as scripts/gee_phase1_agb_proxy.py

# Wave: analysis config and methodology. Relocated to app/domain/analysis_config.py
# (shared by the catalog and this compute path) - re-imported under their
# original names here so every call site below is unchanged.
_ESRI_LULC_YEARS = analysis_config._ESRI_LULC_YEARS
_MODIS_YEARS = analysis_config._MODIS_YEARS


def _catalog_entries_with_live_config() -> list[dict[str, Any]]:
    """CATALOG as plain dicts, with each vegetation index's `config["year_max"]`
    (declared `None` in the static catalog - see analysis_catalog.py's own
    `_VEG_INDEX_CONFIG`) overlaid with the REAL live ceiling
    (`max(current_veg_index_years())`) - evaluated fresh on every call, same
    "never freeze a value that changes every year" reasoning
    `current_veg_index_years()` itself already documents. This is how
    "current-year exclusion still applies" reaches the UI: the frontend is
    always told the real eligible ceiling, never left to guess or hardcode
    one. Returns new dicts; never mutates CATALOG, a shared module-level
    constant every request reads."""
    live_year_max = max(_current_veg_index_years())
    entries = []
    for entry in CATALOG:
        config = entry.get("config")
        if config is not None and config.get("year_max") is None:
            entry = {**entry, "config": {**config, "year_max": live_year_max}}
        entries.append(entry)
    return entries


def _result_out_from_row(row: dict[str, Any]) -> AnalysisResultOut:
    """Constructs explicitly rather than `AnalysisResultOut(**row)` - the DB
    row carries a `params_key` column (Wave: analysis config and
    methodology) that isn't a DTO field; `resolved_params` is that column
    decoded back into the params dict that produced it (`None` for an
    unconfigured analysis_id or a pre-this-wave legacy row - see
    analysis_config.decode_params_key's own docstring)."""
    return AnalysisResultOut(
        project_id=row["project_id"], analysis_id=row["analysis_id"],
        computed_at=row["computed_at"], stats=row["stats"], legend=row["legend"],
        tile_url_template=row["tile_url_template"],
        resolved_params=analysis_config.decode_params_key(row["params_key"]),
    )


class GEEAnalysisService:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- catalog / cache metadata - no GEE calls ----

    def list_catalog(self) -> list[dict[str, Any]]:
        return _catalog_entries_with_live_config()

    def get_project_analyses(
        self, project_id: UUID, user: CurrentUser
    ) -> ProjectAnalysisCatalog:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            computed_at_by_id = AnalysisResultRepository(cur).list_for_project(project_id)
        analyses = [
            ProjectAnalysisSummary(**entry, computed_at=computed_at_by_id.get(entry["id"]))
            for entry in _catalog_entries_with_live_config()
        ]
        return ProjectAnalysisCatalog(project_id=project_id, analyses=analyses)

    def get_result(
        self,
        project_id: UUID,
        analysis_id: str,
        user: CurrentUser,
        request_params: dict[str, Any] | None = None,
    ) -> AnalysisResultOut:
        """`request_params` (Wave: analysis config and methodology) is only
        meaningful for the 7 configurable ids, and only when the caller
        actually sent something - an empty/absent `request_params` means
        "whatever's already there" (params_key=None, most recent across
        variants), same as every pre-existing caller (report generation, a
        plain GET) already expects. A caller with a SPECIFIC configuration in
        hand (the Analysis panel checking whether the currently-selected
        picker state is cached) gets exact-match-or-404, never a different
        variant silently substituted - `resolve_and_validate` still raises
        the same specific rejection a refresh would for an unsupported
        combination."""
        entry = get_catalog_entry(analysis_id)
        params_key = None
        if entry is not None and "config" in entry and request_params:
            resolved = analysis_config.resolve_and_validate(
                analysis_id, entry["config"], request_params
            )
            params_key = analysis_config.params_key(analysis_id, resolved)
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            row = AnalysisResultRepository(cur).get(project_id, analysis_id, params_key)
        if row is None:
            raise NotFoundError("This analysis has not been computed for this project yet.")
        return _result_out_from_row(row)

    def get_point_value(
        self,
        project_id: UUID,
        analysis_id: str,
        lon: float,
        lat: float,
        actor: CurrentUser,
        year: int | None = None,
    ) -> AnalysisPointValue:
        """Identify-tool support: the GEE-layer analog of
        TileService.read_pixel for uploaded COGs. `require_project_view`
        (not `_upload`) - same read-only tier as get_result()/get_pixel(),
        since clicking a pixel costs the viewer nothing.

        For "async"-execution analyses this refuses to compute anything IF
        no result is cached yet: recomputing an index on every map click
        would silently cost the same 5-59s _annual_index_series() timing
        documented at the top of this module, for a "quick pixel click"
        interaction that has to feel instant. The clear, cheap check instead
        is "has this ever been run" (the same computed_at the catalog list
        already surfaces) - if not, the caller (Identify's popup) shows
        "run this analysis first" rather than either hanging or querying a
        result that doesn't exist yet."""
        entry = get_catalog_entry(analysis_id)
        if entry is None:
            raise NotFoundError("Unknown analysis.")
        if entry["status"] != "available":
            raise ValidationError(f"'{entry['name']}' is not implemented yet.")

        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, actor)
            repo = AnalysisResultRepository(cur)
            boundary_geojson = repo.get_project_boundary_geojson(project_id)
            if boundary_geojson is None:
                raise ValidationError(
                    "This project has no Boundary layer yet - upload one before running "
                    "an analysis."
                )
            # Wave: AOI clip. Cheapest, most-fundamental gate first, before the
            # async-cached-result check below: a click outside the boundary is
            # a normal, valid interaction (not an error) now that the map tile
            # itself is clipped and legitimately renders nothing there - GEE
            # would otherwise happily return a real value for a point the map
            # visually shows as empty, which is confusing, not "working as
            # designed". Distinct from "not yet computed" (ValidationError
            # below) and "no data at this pixel" (value=None) - three
            # different reasons a click can come back with nothing.
            if not repo.boundary_contains_point(project_id, lon, lat):
                return AnalysisPointValue(
                    analysis_id=analysis_id, lon=lon, lat=lat, outside_boundary=True
                )
            canopy_cover_pct = float(ForestDefinitionRepository(cur).get()["canopy_cover_pct"])
            if entry.get("execution") == "async":
                cached = repo.get(project_id, analysis_id)
                if cached is None:
                    raise ValidationError(
                        f"Run '{entry['name']}' first - it hasn't been computed for this "
                        "project yet."
                    )

        # Wave: raw-imagery browsing. `year`, only meaningful for the 3
        # browse ids, MUST be the same year the caller's currently-displayed
        # tile was computed with - the frontend passes through whatever year
        # its last refresh() used, so a click samples the same scene the
        # tile visually shows, not silently "most recent" regardless of what
        # was actually rendered.
        request_params = {"year": year} if year is not None else None
        point_result = _compute_point(
            analysis_id, boundary_geojson, canopy_cover_pct, lon, lat, request_params
        )
        return AnalysisPointValue(analysis_id=analysis_id, lon=lon, lat=lat, **point_result)

    # ---- refresh - the only things that actually call GEE ----

    def _prepare_refresh(
        self,
        project_id: UUID,
        analysis_id: str,
        actor: CurrentUser,
        request_params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], float, dict[str, Any] | None]:
        """Shared by refresh() and enqueue_refresh(): catalog/permission/
        boundary validation, all synchronous DB work - done at request time
        for BOTH paths, so a bad request (unknown analysis, not implemented
        yet, no permission, no boundary, an unsupported config combination)
        fails immediately with a normal HTTP error rather than surfacing
        later inside an async job with no one watching.

        `request_params` is resolved+validated (Wave: analysis config and
        methodology) BEFORE the permission/boundary DB round trip, same
        "validate what's knowable from static data first" order the
        catalog/status checks above it already use - an unsupported
        source/masking combination is rejected with its specific reason
        whether or not the caller could even upload to this project.
        Unconfigured ids (`"config" not in entry`) pass `request_params`
        through unchanged - the pre-existing browse-id behavior.

        Wave: VNV Pipeline NDFI go-live. This is now a thin wrapper around
        `app.services.analysis_shared.prepare_analysis_refresh` - that
        logic had zero GEE-specific code in it to begin with, so it was
        extracted there once a second compute source (VNVAnalysisService)
        needed the identical catalog/permission/boundary validation, rather
        than reimplementing a second, slightly-different copy. Signature
        and behavior here are unchanged."""
        return prepare_analysis_refresh(self.db, project_id, analysis_id, actor, request_params)

    def refresh(
        self,
        project_id: UUID,
        analysis_id: str,
        actor: CurrentUser,
        request_params: dict[str, Any] | None = None,
    ) -> AnalysisResultOut:
        """The "sync"-execution path - computes and returns the result inline.
        Never called for an "async"-execution entry (the route sends those to
        enqueue_refresh instead); the assertion below is a guard against that
        ever drifting, not a real user-facing error path.

        `request_params` is resolved by `_prepare_refresh` before this ever
        runs: forwarded as-is for the 3 browse ids (Wave: raw-imagery
        browsing, only `year`), fully resolved+validated for io_lulc/
        modis_lulc (Wave: analysis config and methodology - these are
        "sync"-execution AND configurable, unlike the browse-only framing
        this docstring used to have), and `None` for every other sync entry
        (hansen_gfc/dynamic_world/esa_worldcover), which ignore it
        unchanged."""
        entry, boundary_geojson, canopy_cover_pct, resolved_params = self._prepare_refresh(
            project_id, analysis_id, actor, request_params
        )
        assert entry.get("execution") == "sync", (  # noqa: S101 - internal routing invariant
            f"{analysis_id!r} is execution={entry.get('execution')!r}; route should have used "
            "enqueue_refresh instead"
        )

        # Wave: GEE tile caching. `_compute_cached`, not `_compute` directly -
        # a re-run within the TTL window skips the real GEE call.
        stats, legend, tile_url_template = _compute_cached(
            str(project_id), analysis_id, boundary_geojson, canopy_cover_pct, resolved_params
        )

        with self.db.transaction() as cur:
            row = AnalysisResultRepository(cur).upsert(
                project_id=project_id, analysis_id=analysis_id, computed_by=actor.user_id,
                stats=stats, legend=legend, tile_url_template=tile_url_template,
                params_key=analysis_config.params_key(analysis_id, resolved_params),
            )
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.REFRESH_ANALYSIS, target=analysis_id,
                detail=f"Recomputed '{entry['name']}' for project {project_id}.",
                project_id=project_id,
            )
        return _result_out_from_row(row)

    async def enqueue_refresh(
        self,
        project_id: UUID,
        analysis_id: str,
        actor: CurrentUser,
        jobs: JobService,
        runner: TaskRunner,
        request_params: dict[str, Any] | None = None,
    ) -> UUID:
        """The "async"-execution path - mirrors POST /datasets/upload's own
        job-queue dispatch (app/api/v1/datasets.py) exactly: validate
        synchronously first (same _prepare_refresh as the sync path), submit
        a `jobs` row, then hand the actual GEE compute to the worker
        (app/workers/gee_analysis_jobs.py) without awaiting it. The caller
        polls GET /jobs/{id}, then re-fetches get_result() once it succeeds -
        the job's own row only needs enough to prove it ran, not a duplicate
        copy of the stats (those land in analysis_result via the worker's
        own upsert, the one source of truth get_result() already reads).

        `request_params` (Wave: analysis config and methodology) is resolved+
        validated by `_prepare_refresh` the same way the sync path's
        `refresh()` does, BEFORE the job is even submitted - the 5
        vegetation indices are the only async-execution ids this ever
        matters for; an unsupported combination is rejected here, not
        discovered later inside a job with no one watching."""
        entry, boundary_geojson, canopy_cover_pct, resolved_params = self._prepare_refresh(
            project_id, analysis_id, actor, request_params
        )
        assert entry.get("execution") == "async", (  # noqa: S101 - internal routing invariant
            f"{analysis_id!r} is execution={entry.get('execution')!r}; route should have used "
            "refresh instead"
        )
        # Deferred import: app.workers.gee_analysis_jobs imports _compute FROM
        # this module, so a top-level import here would be circular.
        from app.workers.gee_analysis_jobs import run_gee_analysis_job

        job_id, is_new = jobs.submit(
            user_id=actor.user_id, kind="compute_gee_analysis",
            idempotency_key=None, request_id=None,
        )
        if is_new:
            try:
                await runner.run(
                    run_gee_analysis_job,
                    job_id=str(job_id), project_id=str(project_id), analysis_id=analysis_id,
                    boundary_geojson=boundary_geojson, canopy_cover_pct=canopy_cover_pct,
                    actor=actor.model_dump(mode="json"), request_params=resolved_params,
                )
            except Exception as e:
                # The jobs-row insert already committed (jobs.submit above) - if
                # enqueueing then fails (e.g. Redis unreachable), mark it failed
                # instead of leaving an orphaned `queued` row nothing will ever
                # process. Same recovery datasets.py's upload endpoint does.
                jobs.mark_enqueue_failed(job_id, str(e))
                raise DomainError(
                    "Failed to enqueue this analysis. Please retry."
                ) from e
        return job_id


# ----------------------------------------------------------------- GEE cache
#
# Wave: GEE tile caching. See this module's own docstring (top of file) and
# app/services/gee_tile_cache.py for the full key/TTL design. `_cache_get`/
# `_cache_set` are the ONLY functions that touch Redis - a small,
# monkeypatchable seam (same convention as `init_ee`/`_compute` below), so
# tests exercise cache-hit/-miss/-invalidation logic with an in-memory dict,
# no real Redis needed.


def _redis_client() -> redis_lib.Redis:
    # A fresh client per call rather than a cached singleton: redis-py's own
    # connection pooling already amortizes the real cost (a new TCP
    # connection per call), and a cached client would need explicit
    # invalidation if DMRV_REDIS_URL ever changed within a process's
    # lifetime (e.g. tests). get_settings() itself IS process-cached
    # (app/core/config.py), so this is one attribute lookup, not a fresh
    # env-var parse, on every call.
    return redis_lib.Redis.from_url(get_settings().redis_url, socket_timeout=2.0)


def _cache_get(key: str) -> tuple[dict[str, Any], list[dict[str, Any]] | None, str | None] | None:
    """None on a cache miss OR any Redis failure - caching must never be why
    a request fails, so a broken/unreachable Redis degrades to "always
    recompute", not a 500."""
    try:
        raw = _redis_client().get(key)
    except redis_lib.RedisError as e:
        log.warning("gee_cache.get_failed", key=key, error=str(e))
        return None
    if raw is None:
        return None
    stats, legend, tile_url_template = json.loads(raw)
    return stats, legend, tile_url_template


def _cache_set(
    key: str,
    value: tuple[dict[str, Any], list[dict[str, Any]] | None, str | None],
    ttl: int,
) -> None:
    """Swallows any Redis failure (logged, not raised) - same "caching must
    never break a request" rule as `_cache_get`."""
    try:
        _redis_client().set(key, json.dumps(list(value)), ex=ttl)
    except redis_lib.RedisError as e:
        log.warning("gee_cache.set_failed", key=key, error=str(e))


def _compute_cached(
    project_id: str,
    analysis_id: str,
    boundary_geojson: dict[str, Any],
    canopy_cover_pct: float,
    request_params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]] | None, str | None]:
    """The cached front door to `_compute()` - both `refresh()` (sync path)
    and `run_gee_analysis_job` (async path, app/workers/gee_analysis_jobs.py)
    call THIS, never `_compute` directly, so a re-run within the TTL window
    skips the real GEE call entirely (including the 5-59s vegetation-index
    compositing path). `_compute` itself is untouched - it remains the one
    function existing tests monkeypatch to fake GEE out entirely."""
    key = gee_tile_cache.cache_key(project_id, analysis_id, request_params)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    result = _compute(analysis_id, boundary_geojson, canopy_cover_pct, request_params)
    _cache_set(key, result, gee_tile_cache.ttl_seconds(analysis_id, request_params))
    return result


# --------------------------------------------------------------- GEE queries


def _years_from_resolved(resolved_params: dict[str, Any] | None, fallback_year: int) -> list[int]:
    """Turns a resolved single/range params dict
    (analysis_config.resolve_and_validate's output) into the concrete list
    of years _esri_lulc/_modis_lulc should compute. `fallback_year` covers
    the "no params at all" path (existing tests/fixtures calling `_compute`
    directly, or `resolve_and_validate` never having run) - the single
    latest year, same default `resolve_and_validate` itself would produce."""
    if not resolved_params:
        return [fallback_year]
    if resolved_params.get("year_mode") == "range":
        return list(range(resolved_params["year_start"], resolved_params["year_end"] + 1))
    return [resolved_params.get("year", fallback_year)]


def _compute(
    analysis_id: str,
    boundary_geojson: dict[str, Any],
    canopy_cover_pct: float,
    request_params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]] | None, str | None]:
    """The ONLY function that touches the GEE client - init_ee() and
    ee.Geometry() construction both happen here, not in refresh(), so a
    caller can test refresh()'s DB/permission/cache logic by monkeypatching
    this one function alone, with zero GEE credentials/network involved.

    `request_params` is read by the 3 browse branches (`year`, Wave:
    raw-imagery browsing), by io_lulc/modis_lulc (already-resolved
    year/range via `_years_from_resolved`), and by the 5 vegetation indices
    (already-resolved year/range/season/source/masking, forwarded straight
    through to `_annual_index_series`) - all Wave: analysis config and
    methodology. hansen_gfc/dynamic_world/esa_worldcover ignore it entirely,
    unchanged."""
    init_ee()
    boundary = ee.Geometry(boundary_geojson)
    if analysis_id == "hansen_gfc":
        return _hansen_forest_change(boundary, canopy_cover_pct)
    if analysis_id == "dynamic_world":
        return _dynamic_world(boundary)
    if analysis_id == "esa_worldcover":
        return _esa_worldcover(boundary)
    if analysis_id == "io_lulc":
        years = _years_from_resolved(request_params, max(_ESRI_LULC_YEARS))
        return _esri_lulc(boundary, years)
    if analysis_id == "modis_lulc":
        years = _years_from_resolved(request_params, max(_MODIS_YEARS))
        return _modis_lulc(boundary, years)
    if analysis_id == "ndvi":
        return _ndvi_query(boundary, request_params)
    if analysis_id == "evi":
        return _evi_query(boundary, request_params)
    if analysis_id == "savi":
        return _savi_query(boundary, request_params)
    if analysis_id == "mndwi":
        return _mndwi_query(boundary, request_params)
    if analysis_id == "nbr":
        return _nbr_query(boundary, request_params)
    if analysis_id == "ndwi":
        return _ndwi_query(boundary, request_params)
    if analysis_id == "gndvi":
        return _gndvi_query(boundary, request_params)
    if analysis_id == "ndbi":
        return _ndbi_query(boundary, request_params)
    if analysis_id == "ndmi":
        return _ndmi_query(boundary, request_params)
    if analysis_id == "lswi":
        return _lswi_query(boundary, request_params)
    if analysis_id == "bsi":
        return _bsi_query(boundary, request_params)
    if analysis_id == "arvi":
        return _arvi_query(boundary, request_params)
    if analysis_id == "nddi":
        return _nddi_query(boundary, request_params)
    if analysis_id == "cmri":
        return _cmri_query(boundary, request_params)
    if analysis_id == "psri":
        return _psri_query(boundary, request_params)
    if analysis_id == "s2_browse":
        return _s2_browse(boundary, (request_params or {}).get("year"))
    if analysis_id == "s1_browse":
        return _s1_browse(boundary, (request_params or {}).get("year"))
    if analysis_id == "landsat_browse":
        return _landsat_browse(boundary, (request_params or {}).get("year"))
    # refresh() already rejects any non-"available" analysis_id before calling this.
    raise AssertionError(f"no query function wired for {analysis_id!r}")


def _compute_point(
    analysis_id: str,
    boundary_geojson: dict[str, Any],
    canopy_cover_pct: float,
    lon: float,
    lat: float,
    request_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Identify's per-pixel counterpart to _compute() above: same dispatch,
    same init_ee()/boundary construction, but samples ONE point instead of
    reducing over the whole boundary - reuses each analysis's own image
    builder (the _*_image()/_*_latest_image() helpers _compute()'s own
    functions above call) rather than re-deriving the query. get_point_value()
    (GEEAnalysisService) has already confirmed, for async analyses, that a
    cached result exists before this runs - it is never called otherwise.

    `request_params` (Wave: raw-imagery browsing), same `year` key `_compute`
    reads - MUST be the same year the caller's currently-displayed tile was
    computed with (get_point_value forwards its own `year` query param
    through), or a click could return a value for a different year than the
    one the map tile visually shows. Ignored by every branch except the 3
    browse ones, same as `_compute` above."""
    init_ee()
    boundary = ee.Geometry(boundary_geojson)
    point = ee.Geometry.Point([lon, lat])

    if analysis_id == "hansen_gfc":
        value, detail = _hansen_point(point, canopy_cover_pct)
        return {"value": value, "unit": "% tree cover (2000)", "detail": detail}
    if analysis_id == "dynamic_world":
        code, name, color = _classified_point(
            _dynamic_world_image(boundary), "label", DYNAMIC_WORLD_LEGEND, point, scale=10
        )
        return {"value": code, "class_name": name, "class_color": color}
    if analysis_id == "esa_worldcover":
        code, name, color = _classified_point(
            _esa_worldcover_image(), "Map", ESA_WORLDCOVER_LEGEND, point, scale=10
        )
        return {"value": code, "class_name": name, "class_color": color}
    if analysis_id == "io_lulc":
        code, name, color = _classified_point(
            _esri_lulc_latest_image(), "b1", ESRI_LULC_LEGEND, point, scale=10
        )
        return {"value": code, "class_name": name, "class_color": color}
    if analysis_id == "modis_lulc":
        code, name, color = _classified_point(
            _modis_lulc_latest_image(), "LC_Type1", MODIS_IGBP_LEGEND, point, scale=500
        )
        return {"value": code, "class_name": name, "class_color": color}
    if analysis_id in _INDEX_FORMULAS:
        value = _index_point_value(boundary, point, analysis_id)
        return {"value": value, "unit": analysis_id.upper()}
    if analysis_id in _BROWSE_IMAGE_BUILDERS:
        # RGB/dual-pol images have no single scalar "the" value - reported
        # via `detail` (a formatted per-band sample), leaving value/unit/
        # class_name all None. See this function's own docstring on why
        # `request_params` (the SAME year the caller's tile used) matters here.
        year = (request_params or {}).get("year")
        image_builder, band_labels = _BROWSE_IMAGE_BUILDERS[analysis_id]
        img = image_builder(boundary, year)
        sample = img.reduceRegion(
            reducer=ee.Reducer.first(), geometry=point, scale=10, crs=AOI_CRS, bestEffort=True,
        ).getInfo()
        parts = [f"{label}:{_round_or_none(sample.get(band), 1)}" for band, label in band_labels]
        return {"detail": " ".join(parts) if any(sample.get(b) is not None for b, _ in band_labels) else None}
    # get_point_value() already rejects any non-"available" analysis_id before calling this.
    raise AssertionError(f"no point query function wired for {analysis_id!r}")


def _legend_lookup(
    code: int | None, legend: dict[int, tuple[str, str]]
) -> tuple[int | None, str | None, str | None]:
    """The pure code->(name, color) half of _classified_point, split out so
    it's unit-testable (tests/unit/test_gee_point_query.py) without a live or
    faked `ee` session - the SAME legend dict _class_breakdown()/
    legend_entries() use for the stats panel, so Identify's popup and the
    stats chart can never disagree about what a class code means. An
    unrecognized code (a class the legend doesn't list) still returns a
    label rather than silently dropping the pixel's value."""
    if code is None:
        return None, None, None
    name, color = legend.get(code, (f"Unrecognized class {code}", "#999999"))
    return code, name, color


def _classified_point(
    img: ee.Image,
    band: str,
    legend: dict[int, tuple[str, str]],
    point: ee.Geometry,
    scale: int,
) -> tuple[int | None, str | None, str | None]:
    """Samples one discrete-class band at one point, then translates it via
    _legend_lookup."""
    raw = img.select(band).reduceRegion(
        reducer=ee.Reducer.first(), geometry=point, scale=scale, crs=AOI_CRS, bestEffort=True,
    ).get(band).getInfo()
    return _legend_lookup(int(raw) if raw is not None else None, legend)


def _hansen_detail(
    treecover: float | None,
    loss: bool,
    lossyear: int | None,
    gain: bool,
    canopy_cover_pct: float,
) -> tuple[float | None, str | None]:
    """The pure "phrase these four raw GFC band values as one sentence" half
    of _hansen_point, split out so it's unit-testable without GEE. Hansen has
    no discrete class legend (see _hansen_forest_change) - a clicked point
    gets its raw 2000 tree-cover % plus whatever of loss/gain applies there,
    phrased as one human-readable sentence rather than several separate
    ambiguous numbers."""
    if treecover is None:
        return None, None
    is_forest = treecover >= canopy_cover_pct
    parts = ["forest" if is_forest else f"non-forest (below the {canopy_cover_pct:g}% threshold)"]
    if loss:
        parts.append(f"loss detected in {2000 + int(lossyear)}" if lossyear else "loss detected")
    if gain:
        parts.append("gain detected (2000-2012)")
    return float(treecover), ", ".join(parts)


def _hansen_point(point: ee.Geometry, canopy_cover_pct: float) -> tuple[float | None, str | None]:
    gfc = ee.Image("UMD/hansen/global_forest_change_2025_v1_13")
    sample = gfc.select(["treecover2000", "loss", "lossyear", "gain"]).reduceRegion(
        reducer=ee.Reducer.first(), geometry=point, scale=30, crs=AOI_CRS, bestEffort=True,
    ).getInfo()
    return _hansen_detail(
        sample.get("treecover2000"),
        bool(sample.get("loss")),
        sample.get("lossyear"),
        bool(sample.get("gain")),
        canopy_cover_pct,
    )


def _pixel_area_ha() -> ee.Image:
    return ee.Image.pixelArea().divide(10000)


def _histogram_counts(img: ee.Image, band: str, boundary: ee.Geometry, scale: int):
    """Returns the (not-yet-evaluated) ee.ComputedObject for a class-frequency
    histogram - the caller decides when to .getInfo(), so per-year callers
    can combine several years into a single round trip."""
    return img.select(band).reduceRegion(
        reducer=ee.Reducer.frequencyHistogram(), geometry=boundary, scale=scale, crs=AOI_CRS,
        maxPixels=1e10, bestEffort=True,
    ).get(band)


def _class_breakdown(
    class_area_ha: dict[int, float], legend: dict[int, tuple[str, str]]
) -> dict[str, float]:
    """Full legend, in code order, 0.0 for any class absent from this AOI -
    a stable key set across projects/refreshes, not one that shrinks/grows
    with what happens to be present."""
    return {legend[c][0]: round(class_area_ha.get(c, 0.0), 2) for c in sorted(legend)}


def _visualize_discrete(img: ee.Image, legend: dict[int, tuple[str, str]]) -> ee.Image:
    """Remaps class codes to a contiguous 0..n-1 index before visualize() -
    codes like WorldCover's 10/20/.../100 are not evenly spaced, so a plain
    min/max/palette visualize() would misassign colors."""
    codes = sorted(legend.keys())
    colors = [legend[c][1] for c in codes]
    return img.remap(codes, list(range(len(codes)))).visualize(
        min=0, max=len(codes) - 1, palette=colors
    )


def _hansen_forest_change(
    boundary: ee.Geometry, canopy_cover_pct: float
) -> tuple[dict[str, Any], None, str]:
    gfc = ee.Image("UMD/hansen/global_forest_change_2025_v1_13")
    forest_mask = gfc.select("treecover2000").gte(canopy_cover_pct)
    area_ha = _pixel_area_ha()

    totals = (
        area_ha.rename("baseline_ha").updateMask(forest_mask)
        .addBands(area_ha.rename("gain_ha").updateMask(gfc.select("gain")))
        # Wave: partial coverage. Unmasked - every classified Hansen pixel
        # within the boundary, forest or not, the numerator for coverage_pct
        # below. Same single reduceRegion call, not an extra round trip.
        .addBands(area_ha.rename("classified_ha"))
        .reduceRegion(
            reducer=ee.Reducer.sum(), geometry=boundary, scale=30, crs=AOI_CRS,
            maxPixels=1e10, bestEffort=True,
        )
    )
    loss_stack = (
        area_ha.rename("loss_ha")
        .addBands(gfc.select("lossyear").rename("year"))
        .updateMask(forest_mask.And(gfc.select("loss")))
    )
    grouped = loss_stack.reduceRegion(
        reducer=ee.Reducer.sum().group(groupField=1, groupName="year"),
        geometry=boundary, scale=30, crs=AOI_CRS, maxPixels=1e10, bestEffort=True,
    )
    # ONE round trip for both reduceRegions plus the boundary's own real
    # area (Wave: partial coverage - boundary.area() folds into the SAME
    # getInfo() call rather than a separate round trip).
    result = ee.Dictionary({
        "totals": totals, "loss_groups": grouped.get("groups"),
        "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()

    loss_by_year = {
        str(2000 + int(g["year"])): round(float(g["sum"]), 2)
        for g in (result["loss_groups"] or [])
    }
    covered_area_m2 = float(result["totals"].get("classified_ha") or 0) * 10000
    stats = {
        "canopy_cover_threshold_pct": canopy_cover_pct,
        "baseline_forest_area_ha": round(float(result["totals"].get("baseline_ha") or 0), 2),
        "gain_area_ha_2000_2012": round(float(result["totals"].get("gain_ha") or 0), 2),
        "loss_area_ha_by_year": loss_by_year,
        "coverage_pct": _coverage_pct(covered_area_m2, float(result["boundary_area_m2"])),
        "note": (
            "Gain is a whole-period 2000-2012 figure only - this dataset does not "
            "track gain after 2012. Baseline forest area applies the canopy-cover "
            "threshold above (the current forest-definition setting); re-run this "
            "analysis after changing that setting to see the effect."
        ),
    }

    vis = (
        gfc.select("treecover2000").updateMask(forest_mask)
        .visualize(min=0, max=100, palette=["ffffe5", "004529"])
        .blend(
            gfc.select("lossyear").updateMask(gfc.select("loss"))
            .visualize(min=1, max=23, palette=["ffff00", "ff0000"])
        )
        .blend(gfc.select("gain").selfMask().visualize(palette=["00ffff"]))
    )
    # Wave: AOI clip. Clip the VISUALIZED image, not gfc itself - the
    # reduceRegion stats above are already boundary-scoped via `geometry=
    # boundary`; this only stops the map TILE from rendering the whole
    # global Hansen dataset outside the AOI.
    map_id = vis.clip(boundary).getMapId()
    return stats, None, map_id["tile_fetcher"].url_format


def _dynamic_world_image(boundary: ee.Geometry) -> ee.Image:
    end = datetime.utcnow()
    start = end - timedelta(days=365)
    return (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterBounds(boundary)
        .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        .select("label")
        .mode()
    )


def _dynamic_world(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    dw = _dynamic_world_image(boundary)
    # Wave: partial coverage. boundary.area() folds into the SAME getInfo()
    # call as the histogram - still one round trip.
    result = ee.Dictionary({
        "counts": _histogram_counts(dw, "label", boundary, scale=10),
        "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()
    counts = result.get("counts") or {}
    px_area_ha = 100 / 10000.0  # 10m pixel
    class_ha = {int(k): v * px_area_ha for k, v in counts.items()}
    covered_area_m2 = sum(class_ha.values()) * 10000
    stats = {
        "class_area_ha": _class_breakdown(class_ha, DYNAMIC_WORLD_LEGEND),
        "coverage_pct": _coverage_pct(covered_area_m2, float(result["boundary_area_m2"])),
    }
    # Wave: AOI clip - see _hansen_forest_change's own comment on this pattern.
    map_id = _visualize_discrete(dw, DYNAMIC_WORLD_LEGEND).clip(boundary).getMapId()
    return stats, legend_entries(DYNAMIC_WORLD_LEGEND), map_id["tile_fetcher"].url_format


def _esa_worldcover_image() -> ee.Image:
    return ee.ImageCollection("ESA/WorldCover/v200").first()


def _esa_worldcover(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    wc = _esa_worldcover_image()
    # Wave: partial coverage - see _dynamic_world's own comment on this pattern.
    result = ee.Dictionary({
        "counts": _histogram_counts(wc, "Map", boundary, scale=10),
        "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()
    counts = result.get("counts") or {}
    px_area_ha = 100 / 10000.0
    class_ha = {int(k): v * px_area_ha for k, v in counts.items()}
    covered_area_m2 = sum(class_ha.values()) * 10000
    stats = {
        "class_area_ha": _class_breakdown(class_ha, ESA_WORLDCOVER_LEGEND),
        "coverage_pct": _coverage_pct(covered_area_m2, float(result["boundary_area_m2"])),
        "note": "Single 2021 snapshot, not a time series.",
    }
    # Wave: AOI clip - see _hansen_forest_change's own comment on this pattern.
    map_id = _visualize_discrete(wc.select("Map"), ESA_WORLDCOVER_LEGEND).clip(boundary).getMapId()
    return stats, legend_entries(ESA_WORLDCOVER_LEGEND), map_id["tile_fetcher"].url_format


def _esri_lulc_collection() -> ee.ImageCollection:
    return ee.ImageCollection("projects/sat-io/open-datasets/landcover/ESRI_Global-LULC_10m_TS")


def _esri_lulc_year_mosaic(coll: ee.ImageCollection, year: int) -> ee.Image:
    # Tiled by MGRS tile; a project boundary may span more than one tile,
    # so mosaic every tile tagged with this year rather than picking one.
    return coll.filter(ee.Filter.stringContains("system:index", str(year))).mosaic()


def _esri_lulc_latest_image() -> ee.Image:
    # Identify's own image - always the true dataset ceiling, independent of
    # whatever `years` a refresh() call resolved to (Identify is out of
    # scope for Wave: analysis config and methodology, see analysis_config.py).
    return _esri_lulc_year_mosaic(_esri_lulc_collection(), max(_ESRI_LULC_YEARS)).select("b1")


def _years_label(years: list[int]) -> str:
    """"2023" for a single year, "2018-2020" for a range - shared by every
    note/methodology field below that needs to say which years a call
    actually covered."""
    return str(years[0]) if len(years) == 1 else f"{min(years)}-{max(years)}"


def _land_cover_methodology(
    dataset: str, years: list[int], years_available: tuple[int, int], resolution_m: int
) -> dict[str, Any]:
    """Pure - no `ee` involved - so this is unit-testable directly, unlike
    _esri_lulc/_modis_lulc themselves (real `ee.Dictionary(...).getInfo()`
    calls need a live authenticated GEE session, same constraint
    tests/unit/test_gee_point_query.py's own docstring documents for
    _compute_point). Every value here is user-facing: a spelled-out dataset
    name, not the raw GEE collection id. `years_available` is formatted as
    a "min-max" range string, not a bare `[min, max]` list - caught by
    manual UI review: the frontend's generic list renderer joins arrays
    with ", ", so a 2-item list read as "2017, 2023" (two specific years),
    not the range it actually means; `years_computed` stays a real list
    since it's genuinely one-or-more individual years, not a range."""
    return {
        "dataset": dataset,
        "years_computed": years,
        "years_available": f"{years_available[0]}-{years_available[1]}",
        "resolution_m": resolution_m,
    }


def _esri_lulc(
    boundary: ee.Geometry, years: list[int]
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """`years` (Wave: analysis config and methodology) is caller-resolved -
    `_compute()`'s dispatch turns a request's year/range params into this
    concrete list before calling here. Previously always computed every
    year in `_ESRI_LULC_YEARS` (all 7, unconditionally, on every refresh) -
    a caller now opts into that by explicitly requesting the full range;
    the new default is a single year, the real GEE-cost win this wave is
    partly about."""
    coll = _esri_lulc_collection()
    counts_by_year = {
        str(year): _histogram_counts(_esri_lulc_year_mosaic(coll, year), "b1", boundary, scale=10)
        for year in years
    }
    # Wave: partial coverage. Nested (not flattened alongside the year keys -
    # combined.items() below iterates every top-level key as a "year", so a
    # bare boundary_area_m2 key would break that loop) - still ONE round
    # trip for every year plus the boundary's own area.
    result = ee.Dictionary({
        "counts_by_year": ee.Dictionary(counts_by_year),
        "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()
    combined = result["counts_by_year"]

    px_area_ha = 100 / 10000.0
    class_area_ha_by_year = {}
    covered_area_m2_by_year = {}
    for year, hist in combined.items():
        class_ha = {int(k): v * px_area_ha for k, v in (hist or {}).items()}
        class_area_ha_by_year[year] = _class_breakdown(class_ha, ESRI_LULC_LEGEND)
        covered_area_m2_by_year[year] = sum(class_ha.values()) * 10000
    latest_year_str = str(max(years))
    stats = {
        "class_area_ha_by_year": class_area_ha_by_year,
        # Latest (of the requested `years`) only - matches "latest requested
        # year gets a map tile" below; a per-year coverage_pct would need
        # one per year, not one number.
        "coverage_pct": _coverage_pct(
            covered_area_m2_by_year.get(latest_year_str, 0.0), float(result["boundary_area_m2"])
        ),
        "note": (
            f"Computed for {_years_label(years)} (of {min(_ESRI_LULC_YEARS)}-"
            f"{max(_ESRI_LULC_YEARS)} available)."
        ),
        "methodology": _land_cover_methodology(
            "10m Annual Land Cover (Esri / Impact Observatory)",
            years, (min(_ESRI_LULC_YEARS), max(_ESRI_LULC_YEARS)), 10,
        ),
    }

    latest = _esri_lulc_year_mosaic(coll, max(years)).select("b1")
    # Wave: AOI clip - see _hansen_forest_change's own comment on this pattern.
    map_id = _visualize_discrete(latest, ESRI_LULC_LEGEND).clip(boundary).getMapId()
    return stats, legend_entries(ESRI_LULC_LEGEND), map_id["tile_fetcher"].url_format


def _modis_lulc_collection() -> ee.ImageCollection:
    return ee.ImageCollection("MODIS/061/MCD12Q1").select("LC_Type1")


def _modis_lulc_year_image(coll: ee.ImageCollection, year: int) -> ee.Image:
    return coll.filterDate(f"{year}-01-01", f"{year + 1}-01-01").first()


def _modis_lulc_latest_image() -> ee.Image:
    # Identify's own image - see _esri_lulc_latest_image's own comment on
    # why this stays pinned to the dataset ceiling, not a resolved `years`.
    return _modis_lulc_year_image(_modis_lulc_collection(), max(_MODIS_YEARS))


def _modis_lulc(
    boundary: ee.Geometry, years: list[int]
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """`years` (Wave: analysis config and methodology) - see _esri_lulc's own
    docstring, identical reasoning: caller-resolved, previously always all
    23 years unconditionally."""
    coll = _modis_lulc_collection()
    counts_by_year = {
        str(year): _histogram_counts(
            _modis_lulc_year_image(coll, year), "LC_Type1", boundary, scale=500
        )
        for year in years
    }
    # Wave: partial coverage - see _esri_lulc's own comment on why this is
    # nested rather than flattened alongside the year keys.
    result = ee.Dictionary({
        "counts_by_year": ee.Dictionary(counts_by_year),
        "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()
    combined = result["counts_by_year"]

    px_area_ha = (500 * 500) / 10000.0
    class_area_ha_by_year = {}
    covered_area_m2_by_year = {}
    for year, hist in combined.items():
        class_ha = {int(k): v * px_area_ha for k, v in (hist or {}).items()}
        class_area_ha_by_year[year] = _class_breakdown(class_ha, MODIS_IGBP_LEGEND)
        covered_area_m2_by_year[year] = sum(class_ha.values()) * 10000
    latest_year_str = str(max(years))
    stats = {
        "class_area_ha_by_year": class_area_ha_by_year,
        "coverage_pct": _coverage_pct(
            covered_area_m2_by_year.get(latest_year_str, 0.0), float(result["boundary_area_m2"])
        ),
        "note": (
            f"Computed for {_years_label(years)} (of {min(_MODIS_YEARS)}-{max(_MODIS_YEARS)} "
            "available). 500m resolution - much coarser than the 10m land-cover products "
            "above. Use for multi-year trend context, not microlandscape-scale area."
        ),
        "methodology": _land_cover_methodology(
            "MODIS Land Cover Type (MCD12Q1, IGBP classification)",
            years, (min(_MODIS_YEARS), max(_MODIS_YEARS)), 500,
        ),
    }

    latest = _modis_lulc_year_image(coll, max(years))
    # Wave: AOI clip - see _hansen_forest_change's own comment on this pattern.
    map_id = _visualize_discrete(latest, MODIS_IGBP_LEGEND).clip(boundary).getMapId()
    return stats, legend_entries(MODIS_IGBP_LEGEND), map_id["tile_fetcher"].url_format


# ----------------------------------------------- vegetation/water/burn indices


# Wave: analysis config and methodology. Relocated to
# app/domain/analysis_config.py - re-imported under their original names
# here so every call site below is unchanged.
_VEG_SEASON_START_MD = analysis_config._VEG_SEASON_START_MD
_VEG_SEASON_END_MD = analysis_config._VEG_SEASON_END_MD
_VEG_CLOUD_BAND = analysis_config._VEG_CLOUD_BAND
_VEG_CLOUD_THRESHOLD = analysis_config._VEG_CLOUD_THRESHOLD

# All five indices (NDVI/EVI/SAVI/MNDWI/NBR) are defined on the same natural
# range, ~-1..1 (already the assumption baked into this module's map-tile
# visualize(min=-1, max=1, ...) below) - so the per-year distribution
# histogram uses a FIXED [-1, 1] range with a FIXED bin count
# (ee.Reducer.fixedHistogram), not GEE's default ee.Reducer.histogram(),
# which is equal-COUNT/data-driven: its bucket boundaries are derived from
# whatever min/max actually occurred THAT year, so two years' histograms
# would use different bin edges and couldn't be overlaid or diffed on one
# chart - defeating the entire point of a year-over-year distribution field.
# 20 bins over [-1, 1] gives a fixed 0.1-wide bucket - fine enough that
# common domain thresholds (e.g. NDVI's ~0.4 "healthy vegetation" cutoff)
# land cleanly on a bin edge rather than being smeared across one, coarse
# enough that per-bin pixel counts stay meaningful even for a small
# microlandscape boundary (10m pixels over a few dozen hectares is only a
# few thousand pixels total; Sturges'/Scott's rule would suggest a similar
# ballpark - low teens to ~20 bins - for that sample size, so 20 is not an
# arbitrary round number, it's within the range those rules would suggest
# while also being a clean, memorable, easy-to-explain fixed width).
_VEG_INDEX_HISTOGRAM_RANGE = (-1.0, 1.0)
_VEG_INDEX_HISTOGRAM_BINS = 20

# Wave: composite indices. CMRI = NDVI - NDWI is a plain DIFFERENCE (not a
# ratio) of two [-1,1] indices, so its true range is [-2, 2] - verified live
# against real GEE data: on an ordinary vegetated test boundary (no water at
# all), NDVI-NDWI landed around 1.18, and masking it to the shared [-1, 1]
# range excluded ~98% of pixels (10018 of ~10201), leaving a boundary mean
# computed from a tiny, unrepresentative sliver. This is a genuinely wider
# natural range for this ONE index, not a rare edge case the way EVI's
# denominator blowups are - hence its own override here rather than widening
# the shared range for all 14 indices (which would halve every other index's
# histogram resolution for no reason).
#
# NDDI = (NDVI - NDMI) / (NDVI + NDMI) is a RATIO, unlike CMRI, but its
# denominator can still shrink toward zero whenever NDVI and NDMI have
# opposite signs with close magnitudes - common on ordinary land (positive
# NDVI over vegetation, slightly negative NDMI under dry conditions), not a
# rare artifact the way EVI's blowups are. Verified live: on the same test
# boundary, the RAW (unmasked) NDDI distribution had ~97% of pixels (p2-p99)
# clustered at a single value (~1.11, just outside the shared [-1, 1]) with
# a genuine unbounded tail beyond that (observed range -77168 to 1330, no
# clean fixed bound the way CMRI has). [-3, 3] was chosen empirically:
# recovers most of the wrongly-excluded well-behaved bulk (79.6% -> 93.6%
# in-range on the test boundary) while staying well short of where the real
# outlier tail starts (observed 1st percentile already at -24.6) - a data-
# informed choice, not a mathematically exact bound like CMRI's [-2, 2]. No
# denominator-near-zero guard was added (a deliberate choice, not an
# oversight) - genuine extreme outliers can still occasionally fall inside
# [-3, 3] on a different boundary; `out_of_range_pixel_count` remains the
# disclosure mechanism for whatever this fixed range doesn't catch.
#
# Every other index keeps the shared [-1, 1] default (looked up via
# `_valid_range_for`, not listed individually).
_INDEX_VALID_RANGE: dict[str, tuple[float, float]] = {
    "cmri": (-2.0, 2.0),
    "nddi": (-3.0, 3.0),
}


def _valid_range_for(index_id: str) -> tuple[float, float]:
    return _INDEX_VALID_RANGE.get(index_id, _VEG_INDEX_HISTOGRAM_RANGE)


# Diverging red-yellow-green - low (sparse/no vegetation) to high (dense vegetation).
# Same ramp for all 5 indices: they all range roughly -1..1 with "more vegetation/
# water/burn signal" at the high end, and reusing one palette keeps every index's
# map view visually consistent rather than inventing a new ramp per formula.
_VEG_INDEX_PALETTE = [
    "a50026", "d73027", "f46d43", "fdae61", "fee08b",
    "d9ef8b", "a6d96a", "66bd63", "1a9850", "006837",
]

# Wave: analysis config and methodology. Relocated to
# app/domain/analysis_config.py (the catalog needs this too, to serve a live
# `year_max` to the UI) - re-imported under their original names here so
# every call site below is unchanged.
_VEG_INDEX_FIRST_YEAR = analysis_config._VEG_INDEX_FIRST_YEAR
_current_veg_index_years = analysis_config.current_veg_index_years


def _s2_reflectance_composite(
    boundary: ee.Geometry,
    year: int,
    season_start_md: str = _VEG_SEASON_START_MD,
    season_end_md: str = _VEG_SEASON_END_MD,
) -> ee.Image:
    """Cloud-masked (Cloud Score+, same mask/threshold as
    scripts/gee_phase1_agb_proxy.py's _s2_composite - reused, not
    reinvented) Sentinel-2 median composite for one calendar year's season
    window, scaled from raw DN to [0,1] reflectance. `season_start_md`/
    `season_end_md` (Wave: analysis config and methodology) default to the
    module constants (the original hardcoded Feb-May window) but are real
    per-request parameters now, resolved+validated by
    analysis_config.resolve_and_validate before this ever runs - this
    function itself does no validation, it just uses whatever window it's
    given. The /10000 is not optional: verified live that S2_SR_HARMONIZED
    bands are raw DN in the thousands, not already [0,1] - EVI's "+1" and
    SAVI's "+0.5" are constants calibrated for reflectance and would be
    silently swamped by unscaled DN values (NDVI/MNDWI/NBR are pure ratios
    and technically unaffected by scale, but this is scaled for all 5 for
    consistency, so no future index added here has to remember which ones
    need it)."""
    start, end = f"{year}-{season_start_md}", f"{year}-{season_end_md}"
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start, end)
        .filterBounds(boundary)
    )
    cs_plus = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")

    def _mask(img: ee.Image) -> ee.Image:
        return img.updateMask(img.select(_VEG_CLOUD_BAND).gte(_VEG_CLOUD_THRESHOLD))

    composite = s2.linkCollection(cs_plus, [_VEG_CLOUD_BAND]).map(_mask).median().clip(boundary)
    return composite.divide(10000)


def _index_stats_reducer(
    histogram_range: tuple[float, float] = _VEG_INDEX_HISTOGRAM_RANGE,
) -> ee.Reducer:
    """One combined reducer -> one reduceRegion call per year, still one
    .getInfo() round trip for the whole series (see _annual_index_series):
    mean (unchanged - stats["series"] reads this), stdDev, min/max, and a
    fixed-range histogram, all computed in a single pass over the pixels
    (sharedInputs=True) rather than four/five separate reduceRegion calls.
    GEE only prefixes a combined reducer's output keys with the band name
    when it needs to - i.e. when the SAME output name would otherwise
    collide across more than one input band. Every sub-reducer here already
    has a distinct output name (mean/stdDev/min/max/histogram), so there is
    nothing to disambiguate and GEE returns them bare: mean/stdDev/min/max/
    histogram, NOT index_mean/index_stdDev/... - verified live against GEE
    (no "index_" prefix appears even though the reduceRegion input image has
    2 bands - see _index_stats_reducer_with_raw_count's own docstring for
    the count() sub-reducer, which is bare "count" for the same reason).
    _annual_index_series unpacks these bare keys below.

    `histogram_range` (Wave: composite indices) defaults to the shared
    [-1, 1] every index but CMRI uses - see `_INDEX_VALID_RANGE`'s own
    comment for why CMRI alone gets a wider [-2, 2]. Bin COUNT
    (`_VEG_INDEX_HISTOGRAM_BINS`) stays fixed at 20 regardless, so a wider
    range means a proportionally wider bin (0.2 instead of 0.1 for CMRI) -
    `index_summary.py`'s clause generators derive bin width from the actual
    returned bin_edges, not the module constant, so this is not a silent
    mismatch there (see that module's own docstring)."""
    return (
        ee.Reducer.mean()
        .combine(ee.Reducer.stdDev(), sharedInputs=True)
        .combine(ee.Reducer.minMax(), sharedInputs=True)
        .combine(
            ee.Reducer.fixedHistogram(
                histogram_range[0],
                histogram_range[1],
                _VEG_INDEX_HISTOGRAM_BINS,
            ),
            sharedInputs=True,
        )
    )


def _index_stats_reducer_with_raw_count(
    histogram_range: tuple[float, float] = _VEG_INDEX_HISTOGRAM_RANGE,
) -> ee.Reducer:
    """`_index_stats_reducer()` plus an UNSHARED `ee.Reducer.count()` that
    consumes a SECOND input band, still in the same single reduceRegion call
    (no extra round trip). Exists to recover, per year, how many pixels this
    module's [-1, 1] range mask (see `_annual_index_series`) excluded -
    without that, EVI/SAVI's occasional out-of-range pixels (a near-zero
    denominator blowing the formula up to an arbitrarily large/negative
    value - see this module's own note in `_annual_index_series`) would be
    silently dropped with no trace of how many there were.

    `sharedInputs=False` on the outer `.combine()` is the whole trick: with
    the DEFAULT `sharedInputs=True`, `count()` would consume the SAME first
    band as the stats reducer (the already range-masked "index" band) and
    just reproduce `histogram`'s own bucket sum - useless for this.
    With `sharedInputs=False`, GEE's combine-reducer convention feeds each
    sub-reducer its OWN slice of the input image's bands, in band order: the
    stats reducer (1 input) gets band 0, `count()` (1 input) gets band 1. The
    caller (`_annual_index_series`) must therefore pass a 2-band image,
    band 0 = the range-masked index ("index"), band 1 = the SAME index
    cloud-masked only, range NOT yet applied ("index_raw") - so this
    reducer's extra output key is bare `count` (no band prefix - see
    `_index_stats_reducer`'s own docstring for why), the "before" total;
    `histogram`'s bucket sum is the "after" total; the difference is
    `out_of_range_pixel_count`.

    Kept as a separate function from `_index_stats_reducer()` (rather than
    folding raw-count into that one) so the existing, already-exhaustive
    shape test for `_index_stats_reducer()`
    (test_index_histogram_shaping.py::test_index_stats_reducer_is_one_
    combine_tree_of_mean_stddev_minmax_histogram) keeps asserting on exactly
    the tree it already knows about, undisturbed by this addition."""
    return _index_stats_reducer(histogram_range).combine(ee.Reducer.count(), sharedInputs=False)


def _shape_index_histogram(rows: list[list[float]] | None) -> dict[str, list[Any]]:
    """Pure Python: turns ee.Reducer.fixedHistogram()'s raw reduceRegion
    output - a list of [bucket_lower_edge, count] rows, `_VEG_INDEX_HISTOGRAM_
    BINS` of them, each `count` from a pass over `_VEG_INDEX_HISTOGRAM_RANGE`
    - into {bin_edges, counts} arrays a frontend chart can draw directly, no
    `ee` import needed. bin_edges has len(counts)+1 entries (every bucket's
    lower edge plus the final bucket's upper edge, derived from the bucket
    width) - the same edge convention numpy.histogram uses, so a consumer
    doesn't have to separately carry the bin width around. No `ee` types
    anywhere in this function - qa-backend-tester/qa-geospatial-validator can
    unit test it directly with a canned rows list, no live GEE session
    needed. Returns empty arrays for `None`/empty input (e.g. a year whose
    composite had zero valid pixels within the boundary after cloud
    masking)."""
    if not rows:
        return {"bin_edges": [], "counts": []}
    lower_edges = [float(r[0]) for r in rows]
    counts = [int(r[1]) for r in rows]
    bin_width = lower_edges[1] - lower_edges[0] if len(lower_edges) > 1 else 0.0
    bin_edges = lower_edges + [lower_edges[-1] + bin_width]
    return {"bin_edges": bin_edges, "counts": counts}


def _round_or_none(value: float | None, ndigits: int = 4) -> float | None:
    return round(value, ndigits) if value is not None else None


def _coverage_pct(covered_area_m2: float, boundary_area_m2: float) -> float:
    """Wave: partial coverage. Real, computed fraction of the AOI a result
    actually covers - never an estimate. Clamped to [0, 100]: a boundary
    polygon rasterized at 10-500m resolution can produce a covered-pixel sum
    fractionally ABOVE the true polygon area at the edge (partial-pixel
    overcounting), which should read as ~100% coverage, not >100%. Returns
    0.0 (not a ZeroDivisionError) for a degenerate zero-area boundary -
    defensive only; refresh()'s own "no boundary -> reject before computing"
    gate upstream means this should never actually happen in production."""
    if boundary_area_m2 <= 0:
        return 0.0
    return round(min(100.0, max(0.0, 100.0 * covered_area_m2 / boundary_area_m2)), 1)


def _veg_index_methodology(
    imagery_source: str,
    cloud_masking: str,
    season_start_md: str,
    season_end_md: str,
    years: list[int],
    formula_desc: str,
    valid_range: tuple[float, float] = _VEG_INDEX_HISTOGRAM_RANGE,
) -> dict[str, Any]:
    """Pure - no `ee` involved, unlike _annual_index_series itself - see
    _land_cover_methodology's own docstring for why that separation matters
    for unit-testability. `imagery_source`/`cloud_masking` are spelled out
    in words only for the one real implemented pair (sentinel2/
    cloud_score_plus); any other value (there is currently no other value
    that reaches here - resolve_and_validate rejects it first) is passed
    through as-is rather than mislabeled.

    `valid_range` (Wave: composite indices) defaults to the shared [-1, 1]
    every index but CMRI uses - see `_INDEX_VALID_RANGE`'s own comment."""
    return {
        "imagery_source": (
            "Sentinel-2 (COPERNICUS/S2_SR_HARMONIZED)"
            if imagery_source == "sentinel2"
            else imagery_source
        ),
        "cloud_masking": (
            f"Cloud Score+ (cs_cdf ≥ {_VEG_CLOUD_THRESHOLD:g})"
            if cloud_masking == "cloud_score_plus"
            else cloud_masking
        ),
        "season_window": f"{season_start_md} to {season_end_md}",
        "years_computed": years,
        "formula": formula_desc,
        "valid_range": list(valid_range),
    }


def _annual_index_series(
    boundary: ee.Geometry, index_id: str, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    """Shared by every vegetation/water/burn index - one composite + one
    index value per requested year, not a single snapshot, matching the
    trend-chart framing these were designed for. `index_id` looks up the
    `(ee.Image -> ee.Image, formula description)` pair (e.g. NDVI's
    normalizedDifference) in `_INDEX_FORMULAS` below, applied to each year's
    reflectance composite, and is also passed to `summarize_index_result()`
    to pick the right index-specific wording.

    `resolved` (Wave: analysis config and methodology) is
    analysis_config.resolve_and_validate's output, already validated by the
    time this runs - `None` (the pre-existing-callers/tests default) means
    the single current-eligible year at the module's original Feb-May
    window, same as calling this with nothing ever did before this wave.
    `years` reuses the same `_years_from_resolved` helper io_lulc/modis_lulc
    use (single value or an inclusive start..end range) - a range's upper
    bound is already guaranteed `<= max(current_veg_index_years())` by
    `resolve_and_validate`, never re-checked or silently clamped here.
    Builds the whole N-year
    computation graph in a Python loop but calls .getInfo() exactly once for
    the whole series - one round trip regardless of year count, same
    convention as the two annual (per-year) analyses above, but still slow
    enough end-to-end (5-59s measured for NDVI, see this module's own
    docstring) that every caller of this function runs async, not sync. The
    per-year reduceRegion now runs a combined reducer (_index_stats_reducer_
    with_raw_count, itself built on _index_stats_reducer) instead of bare
    mean - more work per pixel pass, but still exactly ONE reduceRegion call
    per year and ONE .getInfo() for the whole series, so the "one round
    trip" contract this module's docstring documents is unchanged; only the
    per-call server-side compute cost grows a bit (histogram binning is not
    free), which may push the low end of the previously measured 5-59s range
    up somewhat - not re-measured live here, flagging rather than asserting
    a number.

    Each year's index image is masked to its own natural range (`idx = idx.
    updateMask(idx.gte(range_lo).And(idx.lte(range_hi)))`) BEFORE reduction,
    for every one of the 5 indices uniformly, not just EVI/SAVI (the two
    that most often need it). Why this matters: EVI's formula has a
    denominator that can approach/cross zero under real conditions (thin
    haze/cirrus surviving the cs_cdf>=0.60 cloud mask, cloud edges, cloud
    shadow - exactly the pre-monsoon Feb-May Karnataka window this module
    composites over), sending EVI to arbitrarily large or negative values
    far outside [-1, 1]; SAVI can exceed the range more mildly too (scaled
    reflectance >1.0 at bright/specular pixel edges, since bands are
    UINT16/10000 DN and can exceed 10000 at cloud edges). Before this fix,
    `ee.Reducer.fixedHistogram` silently dropped those out-of-range pixels
    from ITS bucket assignment while `combine(..., sharedInputs=True)` still
    fed them to mean/stdDev/minMax - `sharedInputs=True` only means the
    sub-reducers share the same input TUPLE, not that one sub-reducer's
    internal range-filtering propagates to the others - so the histogram and
    the scalar stats silently disagreed about which pixels they counted.
    Masking (not clamping) up front makes every sub-reducer in the combined
    reducer see the identical pixel set, so that disagreement is gone by
    construction; a masked-out pixel is honestly excluded from every stat,
    the same way cloud-masked pixels already are in this same pipeline -
    a clamped 39.69 -> 1.0 would instead be a fabricated observation.

    How many pixels that range-mask excludes is surfaced, not hidden:
    `distribution[year]["out_of_range_pixel_count"]` (see below) is the
    difference between the cloud-masked-only pixel count and the
    range-masked pixel count, computed by adding ONE extra unshared count()
    band to the SAME reduceRegion call rather than a second round trip -
    see `_index_stats_reducer_with_raw_count`'s own docstring for the
    mechanics.

    Only the LATEST year gets a map tile (one extra getMapId() call) - not
    all N years. A per-year tile for the timeline scrubber to switch between
    was considered; deferred rather than adding N extra getMapId() round
    trips to an already-async path. The scrubber still has a real job: it
    drives which year's point is highlighted on the trend chart, using the
    already-fetched series - no extra backend call for that part."""
    index_band, formula_desc = _INDEX_FORMULAS[index_id]
    years = _years_from_resolved(resolved, max(_current_veg_index_years()))
    season_start_md = (resolved or {}).get("season_start", _VEG_SEASON_START_MD)
    season_end_md = (resolved or {}).get("season_end", _VEG_SEASON_END_MD)
    imagery_source = (resolved or {}).get("imagery_source", "sentinel2")
    cloud_masking = (resolved or {}).get("cloud_masking", "cloud_score_plus")
    per_year_stats = {}
    latest_year = max(years)
    latest_image = None
    histogram_range = _valid_range_for(index_id)
    range_lo, range_hi = histogram_range
    for year in years:
        refl = _s2_reflectance_composite(boundary, year, season_start_md, season_end_md)
        # cloud-masked only, natural range not yet enforced (see docstring above)
        idx_raw = index_band(refl).rename("index")
        idx = idx_raw.updateMask(idx_raw.gte(range_lo).And(idx_raw.lte(range_hi)))
        if year == latest_year:
            latest_image = idx
        stack = idx.addBands(idx_raw.rename("index_raw"))
        per_year_stats[str(year)] = stack.reduceRegion(
            reducer=_index_stats_reducer_with_raw_count(histogram_range), geometry=boundary,
            scale=10, crs=AOI_CRS, maxPixels=1e10, bestEffort=True,
        )
    # Wave: partial coverage. Nested (not flattened alongside the year keys -
    # the loop below iterates every top-level key as a year) - still ONE
    # round trip for every year plus the boundary's own real area.
    result = ee.Dictionary({
        "years": ee.Dictionary(per_year_stats), "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()
    raw = result["years"]
    boundary_area_m2 = float(result["boundary_area_m2"])

    series: dict[str, float | None] = {}
    distribution: dict[str, dict[str, Any]] = {}
    latest_year_coverage_pct = 0.0
    for year, values in raw.items():
        values = values or {}
        # Bare keys, not "index_"-prefixed - see _index_stats_reducer's
        # docstring: GEE only prefixes by band name to disambiguate a
        # colliding output name, and none of these collide.
        mean = _round_or_none(values.get("mean"))
        series[year] = mean
        histogram = _shape_index_histogram(values.get("histogram"))
        in_range_count = sum(histogram["counts"]) if histogram["counts"] else 0
        raw_count = values.get("count")
        if year == str(latest_year):
            # Wave: partial coverage. `count` is the cloud-masked-only pixel
            # count (BEFORE the natural-range mask below) - exactly the real
            # covered-area numerator: how much of the AOI actually had a
            # cloud-free Sentinel-2 pixel this year, at the fixed 10m scale
            # this reduceRegion already runs at.
            covered_area_m2 = (raw_count or 0) * 100
            latest_year_coverage_pct = _coverage_pct(covered_area_m2, boundary_area_m2)
        distribution[year] = {
            "mean": mean,
            "std_dev": _round_or_none(values.get("stdDev")),
            "min": _round_or_none(values.get("min")),
            "max": _round_or_none(values.get("max")),
            "histogram": histogram,
            # Pixels this year's cloud-masked composite had that fell outside
            # the natural [-1, 1] range and were therefore excluded from
            # mean/std_dev/min/max/histogram above, not silently folded into
            # them - see _annual_index_series's own docstring for why this
            # can be nonzero (EVI's near-zero-denominator blowup is the main
            # case; SAVI more mildly). None (not 0) when raw_count itself is
            # unavailable (e.g. a year with literally zero cloud-free pixels).
            "out_of_range_pixel_count": (
                int(raw_count) - in_range_count if raw_count is not None else None
            ),
        }

    stats = {
        "series": series,
        "distribution": distribution,
        "summary": summarize_index_result(index_id, series, distribution),
        # Latest year only - matches "latest year gets a map tile" below;
        # a per-year coverage_pct would need one per year, not one number.
        "coverage_pct": latest_year_coverage_pct,
        "note": (
            f"Boundary-mean of a cloud-masked, {season_start_md} to {season_end_md} "
            f"Sentinel-2 composite per year, {_years_label(years)}. The current "
            f"calendar year is only included once its own {season_end_md} window has "
            "closed (no partial-season composite is ever computed). Sentinel-2 only - "
            "a Landsat extension back to 2013 would need different cloud-masking (no "
            "Cloud Score+ equivalent) and risks a false 'trend break' at the sensor "
            "handoff year, so isn't included in this batch. Each year's pixels are "
            "masked to the natural "
            f"[{range_lo:g}, {range_hi:g}] "
            "index range BEFORE mean/std_dev/min/max/histogram are computed (EVI's "
            "denominator can cross zero under thin haze/cloud edges, sending it far "
            "outside that range; CMRI's own range is wider than every other index's "
            "here - see this module's `_INDEX_VALID_RANGE` - since it is a plain "
            "difference, not a ratio) - `distribution[year][\"out_of_range_pixel_count\"]` "
            "records how many pixels that excluded, per year. `distribution` adds "
            "per-year std dev/min/max and a pixel-value histogram fixed to the "
            f"[{range_lo:g}, {range_hi:g}] "
            f"range in {_VEG_INDEX_HISTOGRAM_BINS} bins (same edges every year, so "
            "bars are directly comparable across years) alongside the existing "
            "boundary-mean `series`. `summary` is a deterministic plain-language "
            "reading of the most recent usable year plus the whole-series trend - "
            "composed in Python from these same numbers "
            "(app/services/index_summary.py), no LLM and no external call, so it "
            "is reproducible from the stored stats. Descriptive only: it is not a "
            "forest-definition, eligibility or carbon determination."
        ),
        # Wave: analysis config and methodology. Purely additive - never
        # touched by report_content.py's identity-checked note/summary
        # above. Every value here is the same real input already driving
        # `note`'s own prose, just structured for the UI panel to render
        # field-by-field instead of parsing free text.
        "methodology": _veg_index_methodology(
            imagery_source, cloud_masking, season_start_md, season_end_md, years, formula_desc,
            histogram_range,
        ),
    }
    map_id = latest_image.visualize(
        min=range_lo, max=range_hi, palette=_VEG_INDEX_PALETTE
    ).getMapId()
    return stats, None, map_id["tile_fetcher"].url_format


# One (formula, description) pair per index, `ee.Image` (scaled reflectance)
# -> `ee.Image` (the index band) plus its human-readable formula text - the
# single definition each of _annual_index_series (full multi-year series,
# for refresh()) and _index_point_value (latest year only, for Identify)
# both build off of, so the band math exists in exactly one place per index.
# The description moved here (Wave: analysis config and methodology) so
# stats["methodology"]["formula"] can read the SAME text a comment used to
# only be readable at the source-code level - a relocation, not a rewrite.
_INDEX_FORMULAS: dict[str, tuple[Any, str]] = {
    "ndvi": (
        lambda refl: refl.normalizedDifference(["B8", "B4"]),
        "NDVI = (NIR - Red) / (NIR + Red). Sentinel-2: NIR=B8, Red=B4.",
    ),
    "evi": (
        lambda refl: refl.expression(
            "2.5 * (NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1)",
            {"NIR": refl.select("B8"), "RED": refl.select("B4"), "BLUE": refl.select("B2")},
        ),
        "EVI = 2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1). "
        "Sentinel-2: NIR=B8, Red=B4, Blue=B2.",
    ),
    "savi": (
        lambda refl: refl.expression(
            "((NIR - RED) / (NIR + RED + L)) * (1 + L)",
            {"NIR": refl.select("B8"), "RED": refl.select("B4"), "L": 0.5},
        ),
        "SAVI = ((NIR - Red) / (NIR + Red + L)) * (1 + L), L=0.5 (soil brightness "
        "correction). Sentinel-2: NIR=B8, Red=B4.",
    ),
    "mndwi": (
        lambda refl: refl.normalizedDifference(["B3", "B11"]),
        "MNDWI = (Green - SWIR1) / (Green + SWIR1). Sentinel-2: Green=B3, SWIR1=B11.",
    ),
    "nbr": (
        lambda refl: refl.normalizedDifference(["B8", "B12"]),
        "NBR = (NIR - SWIR2) / (NIR + SWIR2). Sentinel-2: NIR=B8, SWIR2=B12.",
    ),
    "ndwi": (
        lambda refl: refl.normalizedDifference(["B3", "B8"]),
        "NDWI = (Green - NIR) / (Green + NIR). Sentinel-2: Green=B3, NIR=B8. "
        "McFeeters (1996) - distinct from MNDWI, which substitutes SWIR1 for NIR.",
    ),
    "gndvi": (
        lambda refl: refl.normalizedDifference(["B8", "B3"]),
        "GNDVI = (NIR - Green) / (NIR + Green). Sentinel-2: NIR=B8, Green=B3.",
    ),
    "ndbi": (
        lambda refl: refl.normalizedDifference(["B11", "B8"]),
        "NDBI = (SWIR1 - NIR) / (SWIR1 + NIR). Sentinel-2: SWIR1=B11, NIR=B8.",
    ),
    "ndmi": (
        lambda refl: refl.normalizedDifference(["B8", "B11"]),
        "NDMI = (NIR - SWIR1) / (NIR + SWIR1). Sentinel-2: NIR=B8, SWIR1=B11. "
        "Wilson & Sader (2002) - same formula/band pair as NDII (Hardisky et al. 1983), "
        "kept as one index rather than shipped twice under two names.",
    ),
    "lswi": (
        lambda refl: refl.normalizedDifference(["B8", "B11"]),
        "LSWI = (NIR - SWIR1) / (NIR + SWIR1). Sentinel-2: NIR=B8, SWIR1=B11. Xiao et al. "
        "- mathematically identical band math to NDMI, shipped as its own index for the "
        "flood/wetland-monitoring naming convention (see index_summary.py).",
    ),
    "bsi": (
        lambda refl: refl.expression(
            "((SWIR1 + RED) - (NIR + BLUE)) / ((SWIR1 + RED) + (NIR + BLUE))",
            {
                "SWIR1": refl.select("B11"), "RED": refl.select("B4"),
                "NIR": refl.select("B8"), "BLUE": refl.select("B2"),
            },
        ),
        "BSI = ((SWIR1+Red)-(NIR+Blue)) / ((SWIR1+Red)+(NIR+Blue)). "
        "Sentinel-2: SWIR1=B11, Red=B4, NIR=B8, Blue=B2.",
    ),
    "arvi": (
        lambda refl: refl.expression(
            "(NIR - (2 * RED - BLUE)) / (NIR + (2 * RED - BLUE))",
            {"NIR": refl.select("B8"), "RED": refl.select("B4"), "BLUE": refl.select("B2")},
        ),
        "ARVI = (NIR - (2*Red - Blue)) / (NIR + (2*Red - Blue)), the gamma=1 form of "
        "Kaufman & Tanre (1992). Sentinel-2: NIR=B8, Red=B4, Blue=B2.",
    ),
    "nddi": (
        lambda refl: refl.expression(
            "(NDVI - NDMI) / (NDVI + NDMI)",
            {
                "NDVI": refl.normalizedDifference(["B8", "B4"]),
                "NDMI": refl.normalizedDifference(["B8", "B11"]),
            },
        ),
        "NDDI = (NDVI - NDMI) / (NDVI + NDMI), Gu et al. (2007). NDMI here is Gao "
        "(1996)'s NIR-SWIR1 NDWI, Gu et al.'s own original input (mathematically "
        "identical to this platform's NDMI). The ratio's denominator can approach zero "
        "whenever NDVI and NDMI have opposite signs with close magnitudes - computed and "
        "masked on its own [-3,3] range rather than the shared [-1,1] (see "
        "_INDEX_VALID_RANGE), a data-informed choice, not an exact bound. Sentinel-2: "
        "NDVI=(B8-B4)/(B8+B4), NDMI=(B8-B11)/(B8+B11).",
    ),
    "cmri": (
        lambda refl: refl.expression(
            "NDVI - NDWI",
            {
                "NDVI": refl.normalizedDifference(["B8", "B4"]),
                "NDWI": refl.normalizedDifference(["B3", "B8"]),
            },
        ),
        "CMRI = NDVI - NDWI, Gupta et al. (2018), Combined Mangrove Recognition Index. "
        "NDWI here is McFeeters (1996)'s Green-NIR variant, matching the primary source. "
        "A plain difference (not a ratio) of two [-1,1] indices, so its true range is "
        "[-2,2] - wider than the [-1,1] every other index here shares, so CMRI is "
        "computed and masked on its own [-2,2] range (see _INDEX_VALID_RANGE), not the "
        "shared one. Sentinel-2: NDVI=(B8-B4)/(B8+B4), NDWI=(B3-B8)/(B3+B8).",
    ),
    "psri": (
        lambda refl: refl.expression(
            "(RED - BLUE) / REDEDGE",
            {"RED": refl.select("B4"), "BLUE": refl.select("B2"), "REDEDGE": refl.select("B6")},
        ),
        "PSRI = (Red - Blue) / RedEdge, Merzlyak et al. (1999). Sentinel-2: Red=B4, "
        "Blue=B2, RedEdge=B6 (740nm, closest S2 band to the formula's ~750nm reference - "
        "NOT the standard NIR band B8/B8A every other index here uses). Verified live on "
        "two real boundaries: a vegetated boundary (range roughly -0.23 to 0.46, 100% "
        "in-range) and a real lake (Bellandur, Bengaluru - mixed open water/shoreline, "
        "range roughly -0.49 to 0.66, still 100% in-range; RedEdge reflectance over open "
        "water never dropped below ~0.03, nowhere near the true zero that would blow up "
        "the ratio). Not an exhaustive proof against every land-cover/turbidity/shadow "
        "combination - PSRI uses the shared [-1,1] default range (not its own override, "
        "unlike NDDI/CMRI) because both tested boundaries stayed fully in-range, not "
        "because the denominator is mathematically guaranteed never to approach zero; "
        "the generic `describe_out_of_range` disclosure (index_summary.py) remains the "
        "safety net for any untested scenario where it does.",
    ),
}


def _ndvi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "ndvi", resolved)


def _evi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "evi", resolved)


def _savi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "savi", resolved)


def _mndwi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "mndwi", resolved)


def _nbr_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "nbr", resolved)


def _ndwi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "ndwi", resolved)


def _gndvi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "gndvi", resolved)


def _ndbi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "ndbi", resolved)


def _ndmi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "ndmi", resolved)


def _lswi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "lswi", resolved)


def _bsi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "bsi", resolved)


def _arvi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "arvi", resolved)


def _nddi_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "nddi", resolved)


def _cmri_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "cmri", resolved)


def _psri_query(
    boundary: ee.Geometry, resolved: dict[str, Any] | None = None
) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "psri", resolved)


def _index_point_value(boundary: ee.Geometry, point: ee.Geometry, analysis_id: str) -> float | None:
    """Identify's per-index point sample. Deliberately does NOT reuse
    _annual_index_series (which builds and .getInfo()s ALL years, the
    documented 5-59s round trip) - a pixel click needs only the same
    latest-year image the map tile already shows, so this samples just that
    one year's composite at the clicked point."""
    refl = _s2_reflectance_composite(boundary, max(_current_veg_index_years()))
    formula_fn, _formula_desc = _INDEX_FORMULAS[analysis_id]
    idx = formula_fn(refl).rename("index")
    value = idx.reduceRegion(
        reducer=ee.Reducer.first(), geometry=point, scale=10, crs=AOI_CRS, bestEffort=True,
    ).get("index").getInfo()
    return round(value, 4) if value is not None else None


# ------------------------------------------------------------- raw imagery browsing
#
# Wave: AOI clip / raw-imagery browsing. s2_browse/s1_browse/landsat_browse
# below are BROWSE-ONLY: a single scene (S2/Landsat) or the most recent
# scene (S1, no cloud metric applies to radar) for the selected year, no
# cloud-masked multi-image compositing, no band-math/index formula, no
# cross-sensor math. They deliberately do NOT feed `_INDEX_FORMULAS`/
# `_annual_index_series` and must never be wired into the vegetation-index
# compute path - the Landsat-vs-Sentinel-2 cross-sensor concerns documented
# above (different band names, no Cloud Score+ equivalent, a fake
# "trend break" at the sensor handoff year) apply only to COMPUTE (index
# math across sensors over time), not to raw single-sensor tile display,
# which has none of those failure modes. Landsat 8+9 are MERGED here only to
# shorten achievable revisit (pick whichever satellite has the least-cloud
# scene in the window) - never blended/composited into one image.
#
# Current collection ids (verified, Collection 2 Level 2 for Landsat, never
# a pre-C02 id): COPERNICUS/S2_SR_HARMONIZED, COPERNICUS/S1_GRD (IW mode,
# VV+VH), LANDSAT/LC09/C02/T1_L2 + LANDSAT/LC08/C02/T1_L2.


def _browse_year_bounds(year: int | None) -> tuple[str, str]:
    """None -> a rolling 90-day lookback ending today (a genuinely "latest"
    single scene, not a whole-year composite - mirrors _dynamic_world_image's
    own rolling-window convention above, just shorter since this picks ONE
    scene, not a mode() over a year). An explicit year -> that whole calendar
    year, clipped to not run past today for the still-in-progress current
    year (so the date range never reaches into the future)."""
    if year is None:
        end = datetime.utcnow()
        start = end - timedelta(days=90)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    today = datetime.utcnow().date()
    end_date = date(year + 1, 1, 1)
    if year == today.year:
        end_date = min(end_date, today + timedelta(days=1))
    return f"{year}-01-01", end_date.strftime("%Y-%m-%d")


def _s2_browse_image(boundary: ee.Geometry, year: int | None) -> ee.Image:
    start, end = _browse_year_bounds(year)
    return (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(boundary).filterDate(start, end)
        .sort("CLOUDY_PIXEL_PERCENTAGE")
        .first()
    )


def _s2_browse(boundary: ee.Geometry, year: int | None) -> tuple[dict[str, Any], None, str]:
    img = _s2_browse_image(boundary, year)
    vis = img.visualize(bands=["B4", "B3", "B2"], min=0, max=3000).clip(boundary)
    # Wave: partial coverage. count() over the SAME clipped band this scene
    # actually renders (B4, native 10m) - a real scene rarely covers 100% of
    # an AOI near a tile/swath edge, unlike the always-global land-cover
    # datasets above.
    result = ee.Dictionary({
        "scene_date": ee.Date(img.get("system:time_start")).format("YYYY-MM-dd"),
        "cloud_pct": img.get("CLOUDY_PIXEL_PERCENTAGE"),
        "covered_count": img.select("B4").clip(boundary).reduceRegion(
            reducer=ee.Reducer.count(), geometry=boundary, scale=10, crs=AOI_CRS,
            maxPixels=1e10, bestEffort=True,
        ).get("B4"),
        "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()
    covered_area_m2 = (result.get("covered_count") or 0) * 100  # 10m pixels
    stats = {
        "scene_date": result.get("scene_date"),
        "cloud_pct": _round_or_none(result.get("cloud_pct"), 1),
        "coverage_pct": _coverage_pct(covered_area_m2, float(result["boundary_area_m2"])),
        "note": (
            "Browse only - the least-cloud raw Sentinel-2 scene found for the "
            "selected year (or the last 90 days if no year was given), true-color "
            "(B4/B3/B2). No cloud-masked compositing, no index math - a single real "
            "scene, not the multi-year cloud-free composite the vegetation indices use."
        ),
    }
    map_id = vis.getMapId()
    return stats, None, map_id["tile_fetcher"].url_format


def _s1_browse_image(boundary: ee.Geometry, year: int | None) -> ee.Image:
    start, end = _browse_year_bounds(year)
    return (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(boundary).filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .sort("system:time_start", False)  # most recent first - no cloud metric applies to radar
        .first()
    )


def _s1_browse(boundary: ee.Geometry, year: int | None) -> tuple[dict[str, Any], None, str]:
    img = _s1_browse_image(boundary, year)
    # Standard published false-color Sentinel-1 composite - VV(R)/VH(G)/
    # VV-minus-VH(B), dB scale - a widely-recognized SAR browsing convention,
    # not values invented for this module (no in-repo precedent to match).
    vv, vh = img.select("VV"), img.select("VH")
    composite = ee.Image.cat([vv, vh, vv.subtract(vh)]).rename(["r", "g", "b"])
    vis = composite.visualize(bands=["r", "g", "b"], min=-25, max=0).clip(boundary)
    # Wave: partial coverage - see _s2_browse's own comment on this pattern.
    result = ee.Dictionary({
        "scene_date": ee.Date(img.get("system:time_start")).format("YYYY-MM-dd"),
        "covered_count": vv.clip(boundary).reduceRegion(
            reducer=ee.Reducer.count(), geometry=boundary, scale=10, crs=AOI_CRS,
            maxPixels=1e10, bestEffort=True,
        ).get("VV"),
        "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()
    covered_area_m2 = (result.get("covered_count") or 0) * 100  # 10m pixels
    stats = {
        "scene_date": result.get("scene_date"),
        "coverage_pct": _coverage_pct(covered_area_m2, float(result["boundary_area_m2"])),
        "note": (
            "Browse only - the most recent raw Sentinel-1 GRD IW VV/VH scene found "
            "for the selected year (or the last 90 days if no year was given). No "
            "cloud metric applies to radar, so this is the most recent scene in the "
            "window, not a least-cloud pick like the optical browse layers."
        ),
    }
    map_id = vis.getMapId()
    return stats, None, map_id["tile_fetcher"].url_format


def _landsat_browse_image(boundary: ee.Geometry, year: int | None) -> ee.Image:
    start, end = _browse_year_bounds(year)
    return (
        ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
        .merge(ee.ImageCollection("LANDSAT/LC08/C02/T1_L2"))
        .filterBounds(boundary).filterDate(start, end)
        .sort("CLOUD_COVER")
        .first()
    )


def _landsat_browse(boundary: ee.Geometry, year: int | None) -> tuple[dict[str, Any], None, str]:
    img = _landsat_browse_image(boundary, year)
    # Collection 2 Level 2 scale factors (USGS-documented): SR = DN * 0.0000275 - 0.2
    refl = img.select(["SR_B4", "SR_B3", "SR_B2"]).multiply(0.0000275).add(-0.2)
    vis = refl.visualize(bands=["SR_B4", "SR_B3", "SR_B2"], min=0, max=0.3).clip(boundary)
    # Wave: partial coverage - see _s2_browse's own comment on this pattern.
    # scale=30 - Landsat Collection 2's native resolution, not 10m.
    result = ee.Dictionary({
        "scene_date": ee.Date(img.get("system:time_start")).format("YYYY-MM-dd"),
        "cloud_pct": img.get("CLOUD_COVER"),
        "covered_count": refl.select("SR_B4").clip(boundary).reduceRegion(
            reducer=ee.Reducer.count(), geometry=boundary, scale=30, crs=AOI_CRS,
            maxPixels=1e10, bestEffort=True,
        ).get("SR_B4"),
        "boundary_area_m2": boundary.area(maxError=1),
    }).getInfo()
    covered_area_m2 = (result.get("covered_count") or 0) * 900  # 30m pixels
    stats = {
        "scene_date": result.get("scene_date"),
        "cloud_pct": _round_or_none(result.get("cloud_pct"), 1),
        "coverage_pct": _coverage_pct(covered_area_m2, float(result["boundary_area_m2"])),
        "note": (
            "Browse only - the least-cloud raw Landsat 8/9 Collection 2 Level 2 "
            "scene found for the selected year (or the last 90 days if no year was "
            "given), true-color (SR_B4/SR_B3/SR_B2). Landsat 8 and 9 are merged only "
            "to shorten achievable revisit (whichever satellite has the least-cloud "
            "scene wins) - never blended/composited into one image. No index math, "
            "no cross-sensor compositing with Sentinel-2."
        ),
    }
    map_id = vis.getMapId()
    return stats, None, map_id["tile_fetcher"].url_format


# analysis_id -> (raw-image builder, [(band_name, display_label), ...]) for
# _compute_point's browse branch - samples raw digital numbers (not the
# scaled/visualized values) for a quick per-band Identify readout, since none
# of these images have a single scalar "the" value the way a classified
# land-cover pixel or a vegetation-index pixel does.
_BROWSE_IMAGE_BUILDERS: dict[str, tuple[Any, list[tuple[str, str]]]] = {
    "s2_browse": (_s2_browse_image, [("B4", "R"), ("B3", "G"), ("B2", "B")]),
    "s1_browse": (_s1_browse_image, [("VV", "VV"), ("VH", "VH")]),
    "landsat_browse": (_landsat_browse_image, [("SR_B4", "R"), ("SR_B3", "G"), ("SR_B2", "B")]),
}
