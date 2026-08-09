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
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import ee

from app.core.db import Database
from app.core.errors import DomainError, NotFoundError, ValidationError
from app.domain.analysis_catalog import CATALOG, get_catalog_entry
from app.domain.authz import require_project_upload, require_project_view
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
from app.services.gee_client import init_ee
from app.services.index_summary import summarize_index_result
from app.services.jobs_service import JobService
from app.workers.queue import TaskRunner

AOI_CRS = "EPSG:32643"  # UTM 43N, Karnataka -- same convention as scripts/gee_phase1_agb_proxy.py

# ponytail: fixed year lists rather than discovering each collection's actual
# latest year server-side (an extra getInfo() per request for a number that
# only changes once a year). Bump these annually, or add real "latest
# available year" discovery if that becomes painful.
_ESRI_LULC_YEARS = range(2017, 2024)
_MODIS_YEARS = range(2001, 2024)


class GEEAnalysisService:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- catalog / cache metadata - no GEE calls ----

    def list_catalog(self) -> list[dict[str, Any]]:
        return list(CATALOG)

    def get_project_analyses(
        self, project_id: UUID, user: CurrentUser
    ) -> ProjectAnalysisCatalog:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            computed_at_by_id = AnalysisResultRepository(cur).list_for_project(project_id)
        analyses = [
            ProjectAnalysisSummary(**entry, computed_at=computed_at_by_id.get(entry["id"]))
            for entry in CATALOG
        ]
        return ProjectAnalysisCatalog(project_id=project_id, analyses=analyses)

    def get_result(
        self, project_id: UUID, analysis_id: str, user: CurrentUser
    ) -> AnalysisResultOut:
        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_view(cur, project_id, user)
            row = AnalysisResultRepository(cur).get(project_id, analysis_id)
        if row is None:
            raise NotFoundError("This analysis has not been computed for this project yet.")
        return AnalysisResultOut(**row)

    def get_point_value(
        self, project_id: UUID, analysis_id: str, lon: float, lat: float, actor: CurrentUser
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

        point_result = _compute_point(analysis_id, boundary_geojson, canopy_cover_pct, lon, lat)
        return AnalysisPointValue(analysis_id=analysis_id, lon=lon, lat=lat, **point_result)

    # ---- refresh - the only things that actually call GEE ----

    def _prepare_refresh(
        self, project_id: UUID, analysis_id: str, actor: CurrentUser
    ) -> tuple[dict[str, Any], dict[str, Any], float]:
        """Shared by refresh() and enqueue_refresh(): catalog/permission/
        boundary validation, all synchronous DB work - done at request time
        for BOTH paths, so a bad request (unknown analysis, not implemented
        yet, no permission, no boundary) fails immediately with a normal
        HTTP error rather than surfacing later inside an async job with no
        one watching."""
        entry = get_catalog_entry(analysis_id)
        if entry is None:
            raise NotFoundError("Unknown analysis.")
        if entry["status"] != "available":
            raise ValidationError(f"'{entry['name']}' is not implemented yet.")

        with self.db.connection() as conn, conn.cursor() as cur:
            require_project_upload(cur, project_id, actor)
            boundary_geojson = AnalysisResultRepository(cur).get_project_boundary_geojson(
                project_id
            )
            canopy_cover_pct = float(ForestDefinitionRepository(cur).get()["canopy_cover_pct"])

        if boundary_geojson is None:
            raise ValidationError(
                "This project has no Boundary layer yet - upload one before running an analysis."
            )
        return entry, boundary_geojson, canopy_cover_pct

    def refresh(self, project_id: UUID, analysis_id: str, actor: CurrentUser) -> AnalysisResultOut:
        """The "sync"-execution path - computes and returns the result inline.
        Never called for an "async"-execution entry (the route sends those to
        enqueue_refresh instead); the assertion below is a guard against that
        ever drifting, not a real user-facing error path."""
        entry, boundary_geojson, canopy_cover_pct = self._prepare_refresh(
            project_id, analysis_id, actor
        )
        assert entry.get("execution") == "sync", (  # noqa: S101 - internal routing invariant
            f"{analysis_id!r} is execution={entry.get('execution')!r}; route should have used "
            "enqueue_refresh instead"
        )

        stats, legend, tile_url_template = _compute(analysis_id, boundary_geojson, canopy_cover_pct)

        with self.db.transaction() as cur:
            row = AnalysisResultRepository(cur).upsert(
                project_id=project_id, analysis_id=analysis_id, computed_by=actor.user_id,
                stats=stats, legend=legend, tile_url_template=tile_url_template,
            )
            AuditRepository(cur).record(
                actor_id=actor.user_id, actor_name=actor.username,
                action=AuditAction.REFRESH_ANALYSIS, target=analysis_id,
                detail=f"Recomputed '{entry['name']}' for project {project_id}.",
                project_id=project_id,
            )
        return AnalysisResultOut(**row)

    async def enqueue_refresh(
        self,
        project_id: UUID,
        analysis_id: str,
        actor: CurrentUser,
        jobs: JobService,
        runner: TaskRunner,
    ) -> UUID:
        """The "async"-execution path - mirrors POST /datasets/upload's own
        job-queue dispatch (app/api/v1/datasets.py) exactly: validate
        synchronously first (same _prepare_refresh as the sync path), submit
        a `jobs` row, then hand the actual GEE compute to the worker
        (app/workers/gee_analysis_jobs.py) without awaiting it. The caller
        polls GET /jobs/{id}, then re-fetches get_result() once it succeeds -
        the job's own row only needs enough to prove it ran, not a duplicate
        copy of the stats (those land in analysis_result via the worker's
        own upsert, the one source of truth get_result() already reads)."""
        entry, boundary_geojson, canopy_cover_pct = self._prepare_refresh(
            project_id, analysis_id, actor
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
                    actor=actor.model_dump(mode="json"),
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


# --------------------------------------------------------------- GEE queries


def _compute(
    analysis_id: str, boundary_geojson: dict[str, Any], canopy_cover_pct: float
) -> tuple[dict[str, Any], list[dict[str, Any]] | None, str | None]:
    """The ONLY function that touches the GEE client - init_ee() and
    ee.Geometry() construction both happen here, not in refresh(), so a
    caller can test refresh()'s DB/permission/cache logic by monkeypatching
    this one function alone, with zero GEE credentials/network involved."""
    init_ee()
    boundary = ee.Geometry(boundary_geojson)
    if analysis_id == "hansen_gfc":
        return _hansen_forest_change(boundary, canopy_cover_pct)
    if analysis_id == "dynamic_world":
        return _dynamic_world(boundary)
    if analysis_id == "esa_worldcover":
        return _esa_worldcover(boundary)
    if analysis_id == "io_lulc":
        return _esri_lulc(boundary)
    if analysis_id == "modis_lulc":
        return _modis_lulc(boundary)
    if analysis_id == "ndvi":
        return _ndvi_query(boundary)
    if analysis_id == "evi":
        return _evi_query(boundary)
    if analysis_id == "savi":
        return _savi_query(boundary)
    if analysis_id == "mndwi":
        return _mndwi_query(boundary)
    if analysis_id == "nbr":
        return _nbr_query(boundary)
    # refresh() already rejects any non-"available" analysis_id before calling this.
    raise AssertionError(f"no query function wired for {analysis_id!r}")


def _compute_point(
    analysis_id: str,
    boundary_geojson: dict[str, Any],
    canopy_cover_pct: float,
    lon: float,
    lat: float,
) -> dict[str, Any]:
    """Identify's per-pixel counterpart to _compute() above: same dispatch,
    same init_ee()/boundary construction, but samples ONE point instead of
    reducing over the whole boundary - reuses each analysis's own image
    builder (the _*_image()/_*_latest_image() helpers _compute()'s own
    functions above call) rather than re-deriving the query. get_point_value()
    (GEEAnalysisService) has already confirmed, for async analyses, that a
    cached result exists before this runs - it is never called otherwise."""
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
    # ONE round trip for both reduceRegions.
    result = ee.Dictionary({"totals": totals, "loss_groups": grouped.get("groups")}).getInfo()

    loss_by_year = {
        str(2000 + int(g["year"])): round(float(g["sum"]), 2)
        for g in (result["loss_groups"] or [])
    }
    stats = {
        "canopy_cover_threshold_pct": canopy_cover_pct,
        "baseline_forest_area_ha": round(float(result["totals"].get("baseline_ha") or 0), 2),
        "gain_area_ha_2000_2012": round(float(result["totals"].get("gain_ha") or 0), 2),
        "loss_area_ha_by_year": loss_by_year,
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
    counts = _histogram_counts(dw, "label", boundary, scale=10).getInfo() or {}
    px_area_ha = 100 / 10000.0  # 10m pixel
    class_ha = {int(k): v * px_area_ha for k, v in counts.items()}
    stats = {"class_area_ha": _class_breakdown(class_ha, DYNAMIC_WORLD_LEGEND)}
    # Wave: AOI clip - see _hansen_forest_change's own comment on this pattern.
    map_id = _visualize_discrete(dw, DYNAMIC_WORLD_LEGEND).clip(boundary).getMapId()
    return stats, legend_entries(DYNAMIC_WORLD_LEGEND), map_id["tile_fetcher"].url_format


def _esa_worldcover_image() -> ee.Image:
    return ee.ImageCollection("ESA/WorldCover/v200").first()


def _esa_worldcover(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    wc = _esa_worldcover_image()
    counts = _histogram_counts(wc, "Map", boundary, scale=10).getInfo() or {}
    px_area_ha = 100 / 10000.0
    class_ha = {int(k): v * px_area_ha for k, v in counts.items()}
    stats = {
        "class_area_ha": _class_breakdown(class_ha, ESA_WORLDCOVER_LEGEND),
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
    return _esri_lulc_year_mosaic(_esri_lulc_collection(), max(_ESRI_LULC_YEARS)).select("b1")


def _esri_lulc(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    coll = _esri_lulc_collection()
    counts_by_year = {
        str(year): _histogram_counts(_esri_lulc_year_mosaic(coll, year), "b1", boundary, scale=10)
        for year in _ESRI_LULC_YEARS
    }
    combined = ee.Dictionary(counts_by_year).getInfo()  # ONE round trip for every year

    px_area_ha = 100 / 10000.0
    class_area_ha_by_year = {}
    for year, hist in combined.items():
        class_ha = {int(k): v * px_area_ha for k, v in (hist or {}).items()}
        class_area_ha_by_year[year] = _class_breakdown(class_ha, ESRI_LULC_LEGEND)
    stats = {"class_area_ha_by_year": class_area_ha_by_year}

    latest = _esri_lulc_latest_image()
    # Wave: AOI clip - see _hansen_forest_change's own comment on this pattern.
    map_id = _visualize_discrete(latest, ESRI_LULC_LEGEND).clip(boundary).getMapId()
    return stats, legend_entries(ESRI_LULC_LEGEND), map_id["tile_fetcher"].url_format


def _modis_lulc_collection() -> ee.ImageCollection:
    return ee.ImageCollection("MODIS/061/MCD12Q1").select("LC_Type1")


def _modis_lulc_year_image(coll: ee.ImageCollection, year: int) -> ee.Image:
    return coll.filterDate(f"{year}-01-01", f"{year + 1}-01-01").first()


def _modis_lulc_latest_image() -> ee.Image:
    return _modis_lulc_year_image(_modis_lulc_collection(), max(_MODIS_YEARS))


def _modis_lulc(boundary: ee.Geometry) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    coll = _modis_lulc_collection()
    counts_by_year = {
        str(year): _histogram_counts(
            _modis_lulc_year_image(coll, year), "LC_Type1", boundary, scale=500
        )
        for year in _MODIS_YEARS
    }
    combined = ee.Dictionary(counts_by_year).getInfo()  # ONE round trip for every year

    px_area_ha = (500 * 500) / 10000.0
    class_area_ha_by_year = {}
    for year, hist in combined.items():
        class_ha = {int(k): v * px_area_ha for k, v in (hist or {}).items()}
        class_area_ha_by_year[year] = _class_breakdown(class_ha, MODIS_IGBP_LEGEND)
    stats = {
        "class_area_ha_by_year": class_area_ha_by_year,
        "note": (
            "500m resolution - much coarser than the 10m land-cover products above. "
            "Use for multi-year trend context, not microlandscape-scale area."
        ),
    }

    latest = _modis_lulc_latest_image()
    # Wave: AOI clip - see _hansen_forest_change's own comment on this pattern.
    map_id = _visualize_discrete(latest, MODIS_IGBP_LEGEND).clip(boundary).getMapId()
    return stats, legend_entries(MODIS_IGBP_LEGEND), map_id["tile_fetcher"].url_format


# ----------------------------------------------- vegetation/water/burn indices


_VEG_SEASON_START_MD = "02-01"  # pre-monsoon window - same convention as
_VEG_SEASON_END_MD = "05-31"  # scripts/gee_phase1_agb_proxy.py's usage example
_VEG_CLOUD_BAND = "cs_cdf"
_VEG_CLOUD_THRESHOLD = 0.60

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
# Diverging red-yellow-green - low (sparse/no vegetation) to high (dense vegetation).
# Same ramp for all 5 indices: they all range roughly -1..1 with "more vegetation/
# water/burn signal" at the high end, and reusing one palette keeps every index's
# map view visually consistent rather than inventing a new ramp per formula.
_VEG_INDEX_PALETTE = [
    "a50026", "d73027", "f46d43", "fdae61", "fee08b",
    "d9ef8b", "a6d96a", "66bd63", "1a9850", "006837",
]

_VEG_INDEX_FIRST_YEAR = 2017  # Sentinel-2 available since 2017


def _current_veg_index_years(today: date | None = None) -> range:
    """Which calendar years get a composite this call, evaluated FRESH every
    time (a function, not a module-level constant frozen at import time) -
    this service runs inside a long-lived worker process, so a constant
    computed once at container start would freeze the "current year" at
    whatever it was on startup and never pick up a new year becoming
    eligible without a restart.

    Excludes the current year until ITS OWN pre-monsoon window
    (_VEG_SEASON_START_MD..._VEG_SEASON_END_MD, Feb-May) has fully closed:
    from Jan 1 up to May 31 the current year's Feb-May ImageCollection is
    either empty or only partially populated, and _s2_reflectance_composite's
    `.median()` over zero images returns a bandless ee.Image - the
    `_INDEX_FORMULAS` band math (`.select("B8")`/`.normalizedDifference(...)`)
    then errors on that bandless image. Because `_annual_index_series`
    batches every requested year into ONE `ee.Dictionary(...).getInfo()`
    call, a single bad (not-yet-closed) year would fail the ENTIRE multi-year
    series for all 5 indices at once, not just that year - so this excludes
    it before it ever reaches the compute graph rather than trying to handle
    a bandless image gracefully at reduce time.

    `today` is an injectable parameter (defaults to `date.today()`) purely so
    this is unit-testable against fixed boundary dates (Jan 1, May 31, Jun 1)
    without patching the system clock - see
    tests/unit/test_veg_index_years.py."""
    if today is None:
        today = date.today()
    season_end_md = tuple(int(p) for p in _VEG_SEASON_END_MD.split("-"))  # (month, day)
    last_eligible_year = today.year
    if (today.month, today.day) < season_end_md:
        last_eligible_year -= 1
    return range(_VEG_INDEX_FIRST_YEAR, last_eligible_year + 1)


def _s2_reflectance_composite(boundary: ee.Geometry, year: int) -> ee.Image:
    """Cloud-masked (Cloud Score+, same mask/threshold as
    scripts/gee_phase1_agb_proxy.py's _s2_composite - reused, not
    reinvented) pre-monsoon Sentinel-2 median composite for one calendar
    year, scaled from raw DN to [0,1] reflectance. The /10000 is not
    optional: verified live that S2_SR_HARMONIZED bands are raw DN in the
    thousands, not already [0,1] - EVI's "+1" and SAVI's "+0.5" are
    constants calibrated for reflectance and would be silently swamped by
    unscaled DN values (NDVI/MNDWI/NBR are pure ratios and technically
    unaffected by scale, but this is scaled for all 5 for consistency, so
    no future index added here has to remember which ones need it)."""
    start, end = f"{year}-{_VEG_SEASON_START_MD}", f"{year}-{_VEG_SEASON_END_MD}"
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


def _index_stats_reducer() -> ee.Reducer:
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
    _annual_index_series unpacks these bare keys below."""
    return (
        ee.Reducer.mean()
        .combine(ee.Reducer.stdDev(), sharedInputs=True)
        .combine(ee.Reducer.minMax(), sharedInputs=True)
        .combine(
            ee.Reducer.fixedHistogram(
                _VEG_INDEX_HISTOGRAM_RANGE[0],
                _VEG_INDEX_HISTOGRAM_RANGE[1],
                _VEG_INDEX_HISTOGRAM_BINS,
            ),
            sharedInputs=True,
        )
    )


def _index_stats_reducer_with_raw_count() -> ee.Reducer:
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
    return _index_stats_reducer().combine(ee.Reducer.count(), sharedInputs=False)


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


def _annual_index_series(
    boundary: ee.Geometry, index_id: str
) -> tuple[dict[str, Any], None, str]:
    """Shared by every vegetation/water/burn index - one composite + one
    index value PER YEAR (2017-present), not a single snapshot, matching the
    trend-chart framing these were designed for. `index_id` looks up the
    `ee.Image -> ee.Image` formula (e.g. NDVI's normalizedDifference) in
    `_INDEX_FORMULAS` below, applied to each year's reflectance composite,
    and is also passed to `summarize_index_result()` to pick the right
    index-specific wording. Builds the whole N-year
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
    index_band = _INDEX_FORMULAS[index_id]
    years = _current_veg_index_years()
    per_year_stats = {}
    latest_year = max(years)
    latest_image = None
    range_lo, range_hi = _VEG_INDEX_HISTOGRAM_RANGE
    for year in years:
        refl = _s2_reflectance_composite(boundary, year)
        # cloud-masked only, natural range not yet enforced (see docstring above)
        idx_raw = index_band(refl).rename("index")
        idx = idx_raw.updateMask(idx_raw.gte(range_lo).And(idx_raw.lte(range_hi)))
        if year == latest_year:
            latest_image = idx
        stack = idx.addBands(idx_raw.rename("index_raw"))
        per_year_stats[str(year)] = stack.reduceRegion(
            reducer=_index_stats_reducer_with_raw_count(), geometry=boundary, scale=10,
            crs=AOI_CRS, maxPixels=1e10, bestEffort=True,
        )
    raw = ee.Dictionary(per_year_stats).getInfo()  # ONE round trip for every year

    series: dict[str, float | None] = {}
    distribution: dict[str, dict[str, Any]] = {}
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
        "note": (
            "Boundary-mean of a cloud-masked, pre-monsoon (Feb-May) Sentinel-2 "
            f"composite per year, {min(years)}-{max(years)}. The current calendar "
            "year is only included once its own Feb-May window has closed (no "
            "partial-season composite is ever computed). Sentinel-2 only - a Landsat "
            "extension back to 2013 would need different cloud-masking (no Cloud "
            "Score+ equivalent) and risks a false 'trend break' at the sensor "
            "handoff year, so isn't included in this batch. Each year's pixels are "
            "masked to the natural "
            f"[{_VEG_INDEX_HISTOGRAM_RANGE[0]:g}, {_VEG_INDEX_HISTOGRAM_RANGE[1]:g}] "
            "index range BEFORE mean/std_dev/min/max/histogram are computed (EVI's "
            "denominator can cross zero under thin haze/cloud edges, sending it far "
            "outside that range) - `distribution[year][\"out_of_range_pixel_count\"]` "
            "records how many pixels that excluded, per year. `distribution` adds "
            "per-year std dev/min/max and a pixel-value histogram fixed to the "
            f"[{_VEG_INDEX_HISTOGRAM_RANGE[0]:g}, {_VEG_INDEX_HISTOGRAM_RANGE[1]:g}] "
            f"range in {_VEG_INDEX_HISTOGRAM_BINS} bins (same edges every year, so "
            "bars are directly comparable across years) alongside the existing "
            "boundary-mean `series`. `summary` is a deterministic plain-language "
            "reading of the most recent usable year plus the whole-series trend - "
            "composed in Python from these same numbers "
            "(app/services/index_summary.py), no LLM and no external call, so it "
            "is reproducible from the stored stats. Descriptive only: it is not a "
            "forest-definition, eligibility or carbon determination."
        ),
    }
    map_id = latest_image.visualize(min=-1, max=1, palette=_VEG_INDEX_PALETTE).getMapId()
    return stats, None, map_id["tile_fetcher"].url_format


# One formula per index, `ee.Image` (scaled reflectance) -> `ee.Image`
# (the index band) - the single definition each of _annual_index_series
# (full multi-year series, for refresh()) and _index_point_value (latest
# year only, for Identify) both build off of, so the band math exists in
# exactly one place per index.
_INDEX_FORMULAS: dict[str, Any] = {
    # NDVI = (NIR - Red) / (NIR + Red). S2: NIR=B8, Red=B4.
    "ndvi": lambda refl: refl.normalizedDifference(["B8", "B4"]),
    # EVI = 2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1). S2: NIR=B8, Red=B4, Blue=B2.
    "evi": lambda refl: refl.expression(
        "2.5 * (NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1)",
        {"NIR": refl.select("B8"), "RED": refl.select("B4"), "BLUE": refl.select("B2")},
    ),
    # SAVI = ((NIR - Red) / (NIR + Red + L)) * (1 + L), L=0.5 (soil brightness
    # correction). S2: NIR=B8, Red=B4.
    "savi": lambda refl: refl.expression(
        "((NIR - RED) / (NIR + RED + L)) * (1 + L)",
        {"NIR": refl.select("B8"), "RED": refl.select("B4"), "L": 0.5},
    ),
    # MNDWI = (Green - SWIR1) / (Green + SWIR1). S2: Green=B3, SWIR1=B11.
    "mndwi": lambda refl: refl.normalizedDifference(["B3", "B11"]),
    # NBR = (NIR - SWIR2) / (NIR + SWIR2). S2: NIR=B8, SWIR2=B12.
    "nbr": lambda refl: refl.normalizedDifference(["B8", "B12"]),
}


def _ndvi_query(boundary: ee.Geometry) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "ndvi")


def _evi_query(boundary: ee.Geometry) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "evi")


def _savi_query(boundary: ee.Geometry) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "savi")


def _mndwi_query(boundary: ee.Geometry) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "mndwi")


def _nbr_query(boundary: ee.Geometry) -> tuple[dict[str, Any], None, str]:
    return _annual_index_series(boundary, "nbr")


def _index_point_value(boundary: ee.Geometry, point: ee.Geometry, analysis_id: str) -> float | None:
    """Identify's per-index point sample. Deliberately does NOT reuse
    _annual_index_series (which builds and .getInfo()s ALL years, the
    documented 5-59s round trip) - a pixel click needs only the same
    latest-year image the map tile already shows, so this samples just that
    one year's composite at the clicked point."""
    refl = _s2_reflectance_composite(boundary, max(_current_veg_index_years()))
    idx = _INDEX_FORMULAS[analysis_id](refl).rename("index")
    value = idx.reduceRegion(
        reducer=ee.Reducer.first(), geometry=point, scale=10, crs=AOI_CRS, bestEffort=True,
    ).get("index").getInfo()
    return round(value, 4) if value is not None else None
